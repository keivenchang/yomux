"""Characterization contracts for shared fixture parents."""
from tests.helpers.prompt_corpus import PromptCorpus
from tests.helpers.prompt_corpus import PromptCorpusPreset




def test_prompt_corpus_presets_preserve_format_empty_path_and_raw_text_policies(tmp_path):
    root = tmp_path / "prompt_corpus"
    captures = root / "captures"
    captures.mkdir(parents=True)
    (root / "case.json").write_text('{"raw_capture":"json text"}', encoding="utf-8")
    (root / "empty.yaml").write_text("", encoding="utf-8")
    (root / "raw.txt").write_text("plain text", encoding="utf-8")

    agent_tui = PromptCorpus(root, PromptCorpusPreset.AGENT_TUI)
    auto_approve = PromptCorpus(root, PromptCorpusPreset.AUTO_APPROVE)
    mock_agents = PromptCorpus(root, PromptCorpusPreset.MOCK_AGENTS)

    assert agent_tui.load(root / "case.json") == {"raw_capture": "json text"}
    assert agent_tui.load(root / "empty.yaml") is None
    assert mock_agents.load(root / "empty.yaml") == {}
    assert auto_approve.visible_text(root / "raw.txt") == "plain text"
    assert mock_agents.resolve(captures / "inventory.yaml", "case.yaml") == captures / "case.yaml"
    assert agent_tui.resolve(captures / "inventory.yaml", "case.yaml") == root / "case.yaml"
