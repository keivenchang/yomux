# Archived DOIT.a6-cost-renderer.md - A6 Group B Renderer Defects

## Goal

Archive status: the renderer commits named below are ancestors of the consolidated `yolomux.dev7771` line. The only open row was user-gated release observation, which belongs to the active browser release/soak owner rather than a second renderer implementation queue.

Close the three renderer-only defects left in A6 after the Group A producer became live-correct at `818c6db0`. Producer semantics are correct and must NOT be changed; every fix here is presentation.

## Producer status - settled, do not re-derive

`818c6db0 fix: price Claude Opus 5 usage` added the missing effective-dated `anthropic/claude-opus-5` catalog entry. Production 3600s report: catalog rev 4, 99,686,072 priced tokens, $100.34, **zero unpriced**.

Input attribution is also settled and is NOT a defect: ordinary input is correctly 545 tokens. The 91,721,644 cache-read and 7,713,038 cache-write tokens are separate provider dimensions, not dropped input. Do not "fix" this.

## The three defects, each traced to a line

### 1. A genuinely unpriced value renders as `$0.00`

`static_src/js/yolomux/84_stats_current.js`, `currentStatsMoney()`:

```js
function currentStatsMoney(microUsd) {
  const dollars = microUsd / 1_000_000;
```

`null / 1_000_000` is `0` in JavaScript, so an unpriced `None` from the producer (JSON `null`) renders as `$0.00` - indistinguishable from genuinely free. `undefined` would render `$NaN`. Callers are `currentCostPriceHtml()` and every `currentStatsMetric(..., 'cost')` path.

- [x] Render unpriced as unpriced, never `$0.00`. `efa8285d` fixed Stats Current; `dd7d7d18` preserves nullable Debug/YO!cost display values through summary, row, range, subtotal, usage, text, and HTML renderers. The producer remains unchanged.
- [x] Use `Unpriced` for an absent price across every cost surface; when a known API-list comparison exists, show `Marginal Unpriced · At API list prices $0.00` so an actual zero remains distinguishable. `tests/stats_current_ui.test.js` failed first with the old `$0.00` coercion and then passed; `node-layout` passed in 4.22s at `/tmp/a6-debug-unpriced-node-layout-final.log`.

### 2. `Backfill pending` is a fabricated state

`static_src/js/yolomux/85_debug_panel.js:5588`, `debugGraphCostBackfillText()`:

```js
const state = String(summary?.backfill?.state || 'pending');
```

Measured: the producer emits **no backfill state at all** - `grep -rn backfill --include=*.py yolomux_lib/` matching state/pending/complete/partial/running returns nothing. So the `|| 'pending'` invents a state the client cannot know, and the banner has been asserting "pending" permanently. This is the "no silent defaults" rule in the project error guidance: a missing value substituted with a default produces a confident wrong answer.

- [x] Absent backfill data renders nothing (`efa8285d`); a present producer state still renders its actual status. The renderer does not invent pending/since metadata.
- [x] A richer backfill state remains a Group A producer change; no client default was added.

### 3. The Cost summary header truncates

`static_src/js/yolomux/85_debug_panel.js:5686-5690` builds `heading` by concatenation; observed as `"(At API list prices $0.00, Σ displaye…"`. There is no CSS rule for `yo-cost-current-summary-title` and the string is not `slice()`d, so the clip comes from an ancestor's overflow styling.

- [x] The shared `.js-debug-chart-summary` clipping owner now wraps normally with `overflow-wrap:anywhere` (`efa8285d`), so the cost heading is not silently truncated.

## Acceptance

- [ ] USER-GATED release evidence: authenticated desktop browser journey. The dev7773 listener died at 13:24 because its source-revision identity changed while this worktree was being committed; restarting it before the deployment identity is stabilized would repeat D1. This is not a Group B implementation blocker.
- [x] Rebuilt `static/yolomux.js` from source for both renderer commits; `python3 tools/static_build.py --check` passed after the registered Node lane.

## Constraints

- dev7773 / `groupB-ui` only. A6 itself is tracked in dev7772's `DOIT.md`; do not edit that file.
- You already hold `static/yolomux.js` dirty for the fresh-browser work. Land or commit that first so the bundle rebuild for this item does not tangle with it.
- Do not change producer/`None` semantics, and do not touch the cache-read/cache-write dimensions.

---

No date recorded in this queue; 2026-08-07 is the file mtime, not a landing measurement.
