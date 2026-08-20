# DOIT.p2.e1.agent-tokens-chart-top-n.md - Cap The Agent Tokens Legend At Top-N Plus Others

Reported 2026-08-15 from the YO!stats **Agent tokens/min** chart. Small and bounded: one series-construction site, one aggregation rule, one label.

## Symptom

With 16 agents in the retained window, the legend consumed roughly 40% of the panel — seven wrapped rows of swatches above a chart squeezed into the remainder. Most of those series contribute nothing visible; the rendered bars are dominated by two or three agents while entries like `12798-indrajit-nvext`, `184-vLLM-0_27_1`, and `codex-poison-marker` occupy legend space for no readable signal.

## Cause: there is no cap

`static_src/js/yolomux/85_debug_panel.js:1065`

```js
const displayedItems = [...tokenItems.entries()]
  .filter(([, item]) => item.samples > 0)
  .sort((a, b) => a[1].label.localeCompare(b[1].label) || a[0].localeCompare(b[0]));
```

Every agent with at least one sample becomes its own series, sorted alphabetically by label. There is no top-N selection and no aggregation bucket, so legend size grows without bound with the number of agents that were ever active in the window. The chart is `stacked: true` (`84_debug_observation.js:484`), so the totals stay correct — this is purely a selection and presentation problem.

## Second observation from the same screenshot

**`yo7771-b` appears twice in the legend**, as two separate series.

Series identity is the map `key`, but the legend renders `item.label`, and nothing requires labels to be unique. Two distinct keys carrying the same display name therefore render as two indistinguishable entries. A session rename (`yo7771` -> `yo7771-b`, which happened in this window) is the obvious way to produce that, since the legend also still shows a separate `yo7771`.

This is **not diagnosed** — it is an observation that shares the same construction site. Fold it in only if it is cheap; otherwise split it out rather than growing this queue.

## Plan

- [ ] Rank by contribution, not by name. Select the top-N agents by total tokens in the displayed window; the current alphabetical sort is a display order, not a selection rule, and must not be reused as one.
- [ ] Aggregate every remaining agent into one `Others` series rather than dropping them, so the stacked totals and the `(Σ displayed)` header stay exact. A cap that changes the total is a worse defect than a long legend.
- [ ] Localize the `Others` label through the normal i18n path. A raw literal will fail the i18n raw-key gate.
- [ ] Choose N against the real data rather than picking a round number. State what N is and why, and confirm the legend fits without dominating the panel at the sizes this chart is actually viewed at.
- [ ] Confirm the same treatment is correct for **Model output tokens/min** (`84_debug_observation.js:485`, `dynamicTokenDimension: 'model'`), which is built by the same function and has the same unbounded growth. Apply it there or record why not.
- [ ] Decide what `Others` does on hover and click. It must not pretend to be a single agent; if per-agent detail is needed it belongs in the tooltip or a detail view, not in a fabricated series identity.
- [ ] Investigate the duplicate `yo7771-b` legend entry, or split it into its own queue. Two distinct keys sharing one label render as two identical-looking series; establish whether a rename produces this before changing anything.

## Done Criteria

- [ ] With more than N agents in the window, the legend shows exactly N named series plus `Others`, verified in a real browser.
- [ ] The stacked total and the `(Σ displayed)` header are identical before and after the change for the same window, proven by comparison rather than asserted.
- [ ] With N or fewer agents, no `Others` entry appears at all.
- [ ] The `Others` label is localized and the i18n raw-key gate passes.
- [ ] Canonical gate green, no new Warnings or Errors.

## Completion

Record in `docs/DONE/` with the chosen N and its justification, then delete this queue.
