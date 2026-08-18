# DOIT.p2.e2.markdown-task-list-continuity.md - A Numbered Task Item Breaks Onto Two Lines

**p2 for 0.7.8.** Reported 2026-08-15 from the YOLOmux Markdown preview of `STATUS-REPORT.md`.

## Symptom

In the YOLOmux preview, each Goal-checklist row renders as a bullet, then a checkbox alone on its line, then a blank gap, then the text indented below it. The same file in Cursor renders one continuous line: `• [x] 1. A frozen inventory covers every Markdown file…`.

The checkbox and its text should stay on one line.

## Cause: the number after the checkbox starts a nested ordered list

The source line is:

```
source: - [x] 1. A frozen inventory covers every Markdown file, active DOIT queue, ...
```

Everything after `[x] ` is `1. …`, which CommonMark reads as an **ordered list**. Reproduced against the vendored parser (`static/vendor/marked.min.js`):

```
$ marked.parse('- [x] 1. A frozen inventory covers every Markdown file\n')
<ul>
<li><input checked="" disabled="" type="checkbox"> <ol>
<li>A frozen inventory covers every Markdown file</li>
</ol>
</li>
</ul>
```

versus the same line without the number:

```
<ul>
<li><input checked="" disabled="" type="checkbox"> A frozen inventory covers every Markdown file</li>
</ul>
```

`<ol>` is a block element, so it breaks the line and indents. **`marked` is behaving correctly** — this is not a parser bug and must not be fixed by patching or replacing the parser. Cursor differs only because it does not render GFM task checkboxes here, so no task-item/nested-list structure is ever created.

## The producer, for context

`STATUS-REPORT.md` is generated outside this repository by `~/.claude/skills/progress-report/scripts/write_progress.py:243`:

```python
lines.append(f"- [{mark}] {item['id']}. {item['text']}")
```

That is the exact `- [x] 1. …` pattern. Changing it there would fix this one file, but it is a personal skill outside the YOLOmux tree and **is not in scope for this queue** — do not edit it. Fix the renderer so any file written this way displays correctly, since the pattern is legal Markdown that other documents will also use.

## Plan

- [ ] Reproduce in the product, not only in the parser. Open a Markdown file containing `- [x] 1. text` in the YOLOmux preview and confirm the two-line rendering, so the fix is verified against what users see.
- [ ] Keep the checkbox and its text on one line by presentation only. A single-item ordered list nested directly inside a task-list item should render inline and preserve its number. Do not rewrite the source text, do not pre-process the Markdown into a different structure, and do not swap the parser.
- [ ] Preserve the number. `1.`, `2.`, `3.` are meaningful here — `marked` already emits `<ol start="2">` for the second item, so the rendered output must keep showing the original numbering rather than collapsing to unnumbered text.
- [ ] Confirm ordinary nested lists still indent. A genuine multi-item nested list inside a task item must keep its block layout; only the single-item inline case changes.
- [ ] Confirm the checkbox stays interactive. `88_markdown_preview.js` binds task checkboxes back to source lines through `markdownTaskLineEntries` and `data-source-line`; toggling a checkbox in a numbered item must still edit the right line.

## Done Criteria

- [ ] `- [x] 1. text` renders as one continuous line with the number intact, confirmed in a real browser.
- [ ] Toggling that checkbox still writes to the correct source line, covered by a test.
- [ ] A multi-item nested list inside a task item still renders as an indented block.
- [ ] No change to `static/vendor/marked.min.js` and no source-text rewriting in the preview path.
- [ ] Canonical gate green, no new Warnings or Errors.

## Completion

Record in `docs/DONE/`, then delete this queue.
