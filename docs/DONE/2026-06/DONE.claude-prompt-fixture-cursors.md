# Claude prompt fixture cursors

- Completed and removed `DOIT.claude-fixture-cursor.md`. The missing real-capture files named by the old queue are no longer present, so the durable adoption decision is to keep the current synthetic fixture coverage and add explicit synthesized cursor metadata for the remaining cursor-less Claude fixtures. The root and promoted-captures inventories now have no Claude fixture with `cursor=missing`, and `tests/test_mock_agents.py` includes a regression for that contract. Verification: direct inventory scan reported `all claude fixtures have cursor metadata`, `python3 -m pytest tests/test_mock_agents.py::test_all_claude_prompt_corpus_fixtures_have_cursor_metadata -q` passed, and full `python3 -m pytest tests/test_mock_agents.py -q` passed (`152 passed`).

---

Completed 2026-06-25. Extracted from the 2026-06-25 daily log.
