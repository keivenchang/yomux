# Preview Renderer Sample Index

Open these files directly in YOmux Preview to verify the rendered paths. Example files and their supporting directories use numeric prefixes at the top level of `examples/`.

| Renderer | Sample file | Expected behavior |
|---|---|---|
| `markdown` | `10-markdown.md` | Sanitized Markdown with table, tasks, code, image, link, and Mermaid fence |
| `html` | `11-html.html` | Sandboxed HTML Preview with JavaScript-disabled notice |
| `image` | `12-image.svg` | Direct raw-file image Preview, not inline trusted SVG DOM |
| `pdf` | `13-pdf.pdf` | Three-page PDF in the PDF.js viewer with page navigation and outline |
| `mermaid` | `14-mermaid.mmd` | Direct Mermaid source Preview as sanitized generated SVG image |
| `structured` | `15-structured.json` | Pretty structured JSON Preview |
| `json-lines-table` | `16-structured.jsonl` | Bounded JSONL table Preview with record count |
| `structured` | `17-notebook.ipynb` | Safe notebook summary, outputs hidden |
| `structured` | `18-structured.yaml` | Bounded YAML Preview |
| `structured` | `19-structured.toml` | Bounded TOML Preview |
| `structured` | `20-structured.drawio` | Bounded Draw.io XML Preview |
| `structured` | `21-config.properties` | Bounded config Preview |
| `table` | `22-table.csv` | Bounded CSV table Preview |
| `table` | `23-table.tsv` | Bounded TSV table Preview |
| `graphviz` | `28-diagram.dot` | Locally rendered Graphviz source with zoom and pan |
| `sqlite` | `29-database.sqlite` | Read-only table and schema browser with paged rows |
| `pdf` | `24-small.pdf` | Original one-page PDF under 10 KiB for direct viewing |
| `office` | `25-document.docx` | Multi-page document with a heading, table, and small images, converted locally with LibreOffice |
| `office` | `26-slides.pptx` | Two static slide pages with distinct titles, converted locally with LibreOffice |
| `mixed` | `03-mixed.md` | Combined Markdown, local SVG, tasks, table, Mermaid, links, code, and sanitizer checks |
| `TeX` | `30-tex/` | Numbered TeX source, Markdown math, and HTML math examples |

Supporting SVG files live in `31-assets/`. PlantUML and unsupported or broken-by-design media placeholders are omitted because they do not demonstrate a useful rendered preview.
