# Prompt Corpus Fixture Conventions

All current prompt-corpus capture and synthetic fixtures are stored as wide `200x40` terminal snapshots and must include `width: 200` and `height: 40` metadata.

Use `tests/fixtures/prompt_corpus/capture_prompt_fixture.py` with its default `200x40` size for new real Claude/Codex captures. Use another `--size` only when the fixture is specifically testing width or height behavior, and record that size in the fixture metadata.

Wide fixtures are the canonical source. `mock <case>` and `mockcase <case>` in panes narrower than the source capture re-render from the recorded fixture width to the current pane width. `--dump-fixtures` renders at the fixture source width, normally 200 columns, and must not insert a narrow non-TTY fallback into metadata, paths, or captured rows. Full-width separator-only rows and box borders are rebuilt at the output width so the right edge remains visible. If a `mockcase` pane is at least as wide as the fixture source, it preserves the recorded rows exactly. Re-rendering must not merge menu options, prompts, footers, command/tool rows, or fresh assistant rows.
