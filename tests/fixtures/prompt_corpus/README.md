# Prompt Corpus Fixture Conventions

All current prompt-corpus capture and synthetic fixtures are stored as `78x35` terminal snapshots and must include `width: 78` and `height: 35` metadata.

Use `tests/fixtures/prompt_corpus/capture_prompt_fixture.py` with its default size for new real Claude/Codex captures. Use another `--size` only when the fixture is specifically testing width or height behavior, and record that size in the fixture metadata.

`mockcase <case>` and `--dump-fixtures` preserve the recorded fixture viewport as evidence. Plain live `mock <case>` reconstructs hard-wrapped prose rows from the recorded fixture width, re-renders them to the current pane width, and stretches separator-only rows and box borders that exactly hit the recorded fixture width so the mock looks like a live TUI in wider panes. It must not merge menu options, prompts, footers, command/tool rows, or fresh assistant rows.
