# Preview Renderer Sample Index

Open these files directly in YOLOmux Preview to verify the useful rendered preview paths. This directory is intentionally curated: do not add samples that only demonstrate "same as text" behavior, broken-by-design media placeholders, archive fallbacks, or unsupported placeholders.

| Renderer | Sample file | Expected behavior |
|---|---|---|
| `markdown` | `10-markdown.md` | Sanitized Markdown with table, tasks, code, image, link, and Mermaid fence |
| `html` | `11-html.html` | Sandboxed HTML Preview with JavaScript-disabled notice |
| `image` | `12-image.svg` | Direct raw-file image Preview, not inline trusted SVG DOM |
| `mermaid` | `14-mermaid.mmd` | Direct Mermaid source Preview as sanitized generated SVG image |
| `structured` | `15-structured.json` | Pretty structured JSON Preview |
| `structured` | `16-structured.jsonl` | JSONL Preview with record count |
| `structured` | `17-notebook.ipynb` | Safe notebook summary, outputs hidden |
| `structured` | `18-structured.yaml` | Bounded YAML Preview |
| `structured` | `19-structured.toml` | Bounded TOML Preview |
| `structured` | `20-structured.drawio` | Bounded Draw.io XML Preview |
| `structured` | `21-config.properties` | Bounded config Preview |
| `table` | `22-table.csv` | Bounded CSV table Preview |
| `table` | `23-table.tsv` | Bounded TSV table Preview |
| `mixed` | `03-mixed.md` | Combined Markdown, local SVG, tasks, table, Mermaid, links, code, and sanitizer checks |

Removed on purpose: `.pdf` is blocked by Chrome in the embedded sample path, `.log` and `.patch` look like ordinary text, DOT/PlantUML are not rendered diagrams yet, `.wav`/`.mp4` placeholders only show browser decode failures, and `.zip`/unsupported placeholders are not useful rendering examples.
