import json
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from yolomux_lib.agent_tui import AgentTuiCapture
from yolomux_lib.agent_tui import AgentTuiCursor
from yolomux_lib.agent_tui import capture_agent_pane
from yolomux_lib.agent_tui import classify_agent_pane
from yolomux_lib.agent_tui import clear_composer
from yolomux_lib.agent_tui import cursor_state
from yolomux_lib.agent_tui import read_composer_state
from yolomux_lib.agent_tui import send_prompt


PROMPT_CORPUS_DIR = Path(__file__).resolve().parent / "fixtures" / "prompt_corpus"
PROMOTED_CAPTURE_DIR = PROMPT_CORPUS_DIR / "captures"


def load_structured_fixture(path):
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


def fixture_visible_text(path):
    data = load_structured_fixture(path)
    return str(data.get("raw_capture") or data.get("visible_text") or "")


def load_prompt_corpus_cases():
    inventory = load_structured_fixture(PROMPT_CORPUS_DIR / "inventory.yaml")
    cases = []
    for fixture in inventory["fixtures"]:
        case = dict(fixture)
        case["text"] = fixture_visible_text(PROMPT_CORPUS_DIR / fixture["file"])
        cases.append(case)
    return cases


PROMPT_CORPUS_ASK_CASES = [case for case in load_prompt_corpus_cases() if case["expected"]["ask"]]


def load_promoted_capture_cases():
    inventory_path = PROMOTED_CAPTURE_DIR / "inventory.yaml"
    if not inventory_path.exists():
        return []
    inventory = load_structured_fixture(inventory_path)
    cases = []
    for fixture in inventory["fixtures"]:
        path = PROMOTED_CAPTURE_DIR / fixture["file"]
        data = load_structured_fixture(path)
        cases.append({"id": fixture["id"], "inventory": fixture, "data": data, "path": path})
    return cases


PROMOTED_CAPTURE_CASES = load_promoted_capture_cases()


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(["tmux"], returncode, stdout, stderr)


def cursor_from_capture(data):
    cursor = data.get("cursor") if isinstance(data.get("cursor"), dict) else {}
    return AgentTuiCursor(
        x=int(cursor.get("x") or 0),
        y=int(cursor.get("y") or 0),
        character=str(cursor.get("character") or ""),
        pane_in_mode=bool(cursor.get("pane_in_mode")),
        current_command=str(cursor.get("current_command") or ""),
        error=str(cursor.get("error") or ""),
    )


def test_agent_tui_capture_yaml_files_include_client_version_and_date():
    for path in PROMPT_CORPUS_DIR.rglob("*.yaml"):
        data = load_structured_fixture(path)
        if "raw_capture" not in data:
            continue
        version_slug = data.get("client_version_slug")
        capture_date = data.get("capture_date")
        filename_stem = path.stem
        final_component = filename_stem.rsplit("_", 1)[-1]
        assert data.get("client_version"), path
        assert version_slug, path
        assert capture_date, path
        assert version_slug in path.name
        assert capture_date.replace("-", "") in path.name
        assert final_component.startswith(capture_date.replace("-", "")), path


def test_synthetic_prompt_corpus_cases_are_in_synthetic_dir():
    inventory = load_structured_fixture(PROMPT_CORPUS_DIR / "inventory.yaml")
    for fixture in inventory["fixtures"]:
        path = PROMPT_CORPUS_DIR / fixture["file"]
        data = load_structured_fixture(path)
        is_synthetic = str(data.get("client_version_slug") or "").endswith("-synthetic")
        assert (Path(fixture["file"]).parts[0] == "synthetic") is is_synthetic, fixture["file"]
    assert not list(PROMPT_CORPUS_DIR.rglob("*.json"))
    assert [path.name for path in PROMPT_CORPUS_DIR.glob("*.yaml")] == ["inventory.yaml"]


def claude_composer(text):
    return "\n".join([
        "new task? /clear to save 193.6k tokens",
        "────────────────────────────────────────────────────────────────",
        text,
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])


def codex_composer(text):
    return "\n".join([
        "• Wrote /tmp/hangman.py and verified it.",
        "",
        text,
        "",
        "  gpt-5.5 medium · ~",
    ])


def test_cursor_state_reads_tmux_display_message():
    cursor = cursor_state("%1", display_func=lambda target: completed("12\t3\t>\t0\tclaude\n"))

    assert cursor == AgentTuiCursor(x=12, y=3, character=">", pane_in_mode=False, current_command="claude")


def test_cursor_state_reports_bad_tmux_output():
    cursor = cursor_state("%1", display_func=lambda _target: completed("bad\t3\t>\t0\n"))

    assert cursor.error == "tmux cursor output was not numeric"
    assert cursor.character == ">"


def test_read_composer_state_distinguishes_empty_draft_and_ghost():
    empty = read_composer_state(claude_composer("❯ "))
    draft = read_composer_state(claude_composer("❯ Write tests for @filename"))
    ghost = read_composer_state(claude_composer("❯\xa0commit the DYN_PARSER_DEBUG change"))
    codex_ghost = read_composer_state(codex_composer("\x1b[0;1m›\x1b[0m \x1b[2mSummarize recent commits"))

    assert empty.key == "empty"
    assert empty.accepting is True
    assert draft.key == "draft"
    assert draft.detected_text == "Write tests for @filename"
    assert ghost.key == "ghost"
    assert ghost.accepting is True
    assert codex_ghost.key == "ghost"


def test_read_composer_state_treats_claude_nbsp_text_as_draft_when_cursor_advanced():
    visible = claude_composer("❯\xa0old draft")
    ghost_capture = AgentTuiCapture(target="%1", visible_text=visible, cursor=AgentTuiCursor(x=2, y=31, character="o"))
    draft_capture = AgentTuiCapture(target="%1", visible_text=visible, cursor=AgentTuiCursor(x=11, y=31, character=" "))

    assert read_composer_state(ghost_capture).key == "ghost"
    draft = read_composer_state(draft_capture)
    assert draft.key == "draft"
    assert draft.detected_text == "old draft"
    assert draft.evidence == "cursor-after-suggestion-text"


def test_read_composer_state_handles_claude_plan_mode_footer():
    visible = "\n".join([
        "────────────────────────────────────────────────────────────────",
        "❯\xa0claude-draft-for-yolomux-live-e2e",
        "────────────────────────────────────────────────────────────────",
        "  ⏸ plan mode on (shift+tab to cycle)",
    ])
    capture = AgentTuiCapture(target="%1", visible_text=visible, cursor=AgentTuiCursor(x=35, y=37, character=" "))

    draft = read_composer_state(capture)

    assert draft.key == "draft"
    assert draft.detected_text == "claude-draft-for-yolomux-live-e2e"
    assert draft.evidence == "cursor-after-suggestion-text"


def test_read_composer_state_handles_multiline_history_questions_and_approvals():
    multiline = "\n".join([
        "new task? /clear to save 193.6k tokens",
        "────────────────────────────────────────────────────────────────",
        "❯ Use this context:",
        "",
        "  It's 11:17 PM PDT.",
        "",
        "  Task: add 10 minutes and say if that is right.",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    old_history = "\n".join([
        "❯ what time it is",
        "",
        "  Ran 1 shell command",
        "",
        "● It's 11:17 PM PDT.",
        "",
        "────────────────────────────────────────────────────────────────",
        "❯ ",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    numbered_choice = "\n".join([
        "Which backend should I use?",
        "❯ 1. vLLM",
        "  2. SGLang",
        "Enter to select · ↑/↓ to navigate · Esc to cancel",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    approval = "\n".join([
        "Would you like to run the following command?",
        "$ python3 tools/check.py",
        "❯ 1. Yes",
        "  2. No",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])

    assert read_composer_state(multiline).detected_text == "Use this context: It's 11:17 PM PDT. Task: add 10 minutes and say if that is right."
    assert read_composer_state(old_history).key == "empty"
    assert read_composer_state(numbered_choice).key == "empty"
    assert read_composer_state(approval).key == "empty"


def test_capture_agent_pane_prefers_styled_capture_only_when_plain_text_matches():
    plain = codex_composer("› Summarize recent commits")
    styled = codex_composer("\x1b[0;1m›\x1b[0m \x1b[2mSummarize recent commits")

    capture = capture_agent_pane(
        "%1",
        capture_func=lambda _target, visible_only=False: plain,
        capture_styled_func=lambda _target, visible_only=False: styled,
        cursor_func=lambda _target: AgentTuiCursor(x=1, y=2, character="›"),
    )

    assert capture.visible_text == styled
    assert read_composer_state(capture).key == "ghost"
    assert capture.cursor.x == 1
    assert capture.current_command == ""


def test_classify_agent_pane_upgrades_idle_composer_draft_to_input_draft():
    visible = claude_composer("❯ Write tests for @filename")

    state = classify_agent_pane(
        "%1",
        session="1",
        include_composer=True,
        include_transcript_activity=False,
        capture_func=lambda _target, visible_only=False: visible,
        capture_styled_func=lambda _target, visible_only=False: "",
        prompt_classifier=lambda _target, _visible, _pane, _source: {"visible": False},
        screen_classifier=lambda _visible, _pane_target: {"key": "idle", "text": "", "negative_reason": "idle composer"},
    )

    assert state.screen["key"] == "input-draft"
    assert state.screen["detected_text"] == "Write tests for @filename"
    assert state.composer.key == "draft"


def test_classify_agent_pane_keeps_working_state_over_composer_text():
    visible = "\n".join([
        "✽ Hashing… (3s · ↓ 26 tokens)",
        "",
        "new task? /clear to save 193.6k tokens",
        "────────────────────────────────────────────────────────────────",
        "❯ stale draft-looking text",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])

    state = classify_agent_pane(
        {"pane_target": "%1", "agent_kind": "claude"},
        session="1",
        include_composer=True,
        include_transcript_activity=False,
        capture_func=lambda _target, visible_only=False: visible,
        capture_styled_func=lambda _target, visible_only=False: "",
        prompt_classifier=lambda _target, _visible, _pane, _source: {"visible": False},
        screen_classifier=lambda _visible, _pane_target: {"key": "working", "text": "agent is working"},
    )

    assert state.screen["key"] == "working"
    assert state.reason_code == "busy"


def test_classify_agent_pane_handles_idle_ghost_disconnected_and_transcript_upgrade():
    ghost_plain = codex_composer("› Summarize recent commits")
    ghost_styled = codex_composer("\x1b[0;1m›\x1b[0m \x1b[2mSummarize recent commits")
    idle_ghost = classify_agent_pane(
        {"pane_target": "%1", "agent_kind": "codex"},
        session="1",
        include_composer=True,
        include_transcript_activity=False,
        capture_func=lambda _target, visible_only=False: ghost_plain,
        capture_styled_func=lambda _target, visible_only=False: ghost_styled,
        prompt_classifier=lambda _target, _visible, _pane, _source: {"visible": False},
        screen_classifier=lambda _visible, _pane_target: {"key": "idle", "text": ""},
    )
    disconnected = classify_agent_pane(
        "%missing",
        include_composer=True,
        capture_func=lambda _target, visible_only=False: None,
        capture_styled_func=lambda _target, visible_only=False: "",
    )
    transcript_upgrade = classify_agent_pane(
        {"pane_target": "%1", "agent_kind": "claude"},
        session="1",
        discovered_sessions={"1": object()},
        include_composer=False,
        include_transcript_activity=True,
        capture_func=lambda _target, visible_only=False: claude_composer("❯ "),
        capture_styled_func=lambda _target, visible_only=False: "",
        prompt_classifier=lambda _target, _visible, _pane, _source: {"visible": False},
        screen_classifier=lambda _visible, _pane_target: {"key": "idle", "text": ""},
        transcript_classifier=lambda _info: {"key": "working", "text": "recent transcript tool use"},
    )

    assert idle_ghost.screen["key"] == "idle"
    assert idle_ghost.composer.key == "ghost"
    assert disconnected.reason_code == "disconnected"
    assert transcript_upgrade.screen["key"] == "working"
    assert transcript_upgrade.reason_code == "busy"


def test_classify_agent_pane_marks_questions_as_attention_not_approval():
    state = classify_agent_pane(
        "%1",
        session="1",
        include_composer=False,
        include_transcript_activity=False,
        capture_func=lambda _target, visible_only=False: "Which backend should I use?\n❯ 1. vLLM\n  2. SGLang",
        capture_styled_func=lambda _target, visible_only=False: "",
        prompt_classifier=lambda _target, _visible, _pane, _source: {"visible": False},
        screen_classifier=lambda _visible, _pane_target: {"key": "needs-input", "text": "Which backend should I use?"},
    )

    assert state.prompt["visible"] is False
    assert state.screen["key"] == "needs-input"
    assert state.agent_kind == ""
    assert state.attention_kind == "question"
    assert state.attention_label == "ASK"
    assert state.display["screen_key"] == "needs-input"
    assert state.display["attention_kind"] == "question"
    assert state.display["question_text"] == "Which backend should I use?"
    assert state.reason_code == "needs-input"


def test_classify_agent_pane_returns_approval_action_fields():
    state = classify_agent_pane(
        {"pane_target": "%1", "agent_kind": "claude"},
        session="1",
        include_composer=False,
        include_transcript_activity=False,
        capture_full_for_bash=False,
        capture_func=lambda _target, visible_only=False: "approval pane",
        capture_styled_func=lambda _target, visible_only=False: "",
        prompt_classifier=lambda _target, _visible, _pane, _source: {
            "visible": True,
            "type": "bash",
            "action": "approve",
            "command": "python3 tools/check.py",
            "dangerous": False,
            "selected_option": 1,
            "hash": "abc123",
            "question_text": "Do you want to proceed?",
            "options": [{"index": 1, "label": "Yes"}],
        },
        screen_classifier=lambda _visible, _pane_target: {"key": "approval", "text": "Do you want to proceed?"},
    )

    assert state.agent_kind == "claude"
    assert state.attention_kind == "approval"
    assert state.attention_label == "YOLO?"
    assert state.reason_code == "approval"
    assert state.display["prompt_hash"] == "abc123"
    assert state.approval == {
        "approval_visible": True,
        "approval_type": "bash",
        "approval_action": "approve",
        "selected_option": 1,
        "command": "python3 tools/check.py",
        "dangerous": False,
        "risk": "",
        "rule_input_text": "python3 tools/check.py",
        "prompt_hash": "abc123",
        "source": "pane",
    }


@pytest.mark.parametrize("case", PROMPT_CORPUS_ASK_CASES, ids=lambda case: case["id"])
def test_classify_agent_pane_prompt_corpus_exposes_display_and_approval_fields(case):
    text = case["text"]
    expected = case["expected"]
    state = classify_agent_pane(
        {"pane_target": "%fixture", "agent_kind": expected["agent"]},
        session="fixture",
        prompt_source="pane",
        include_composer=False,
        include_transcript_activity=False,
        capture_func=lambda _target, visible_only=False: text,
        capture_styled_func=lambda _target, visible_only=False: "",
    )

    assert state.screen["key"] == expected["screen_key"]
    assert state.display["screen_key"] == expected["screen_key"]
    assert state.display["prompt_kind"] == expected["prompt_kind"]
    assert state.display["question_text"] == expected["question_text"]
    assert state.display["selected_option"] == expected["selected_option"]
    assert [item["label"] for item in state.display["options"]] == expected["option_labels"]

    if expected["approval_visible"]:
        assert state.reason_code == "approval"
        assert state.attention_kind == "approval"
        assert state.attention_label == "YOLO?"
        assert state.approval["approval_visible"] is True
        assert state.approval["approval_type"] == expected["prompt_type"]
        assert state.approval["approval_action"] == expected["action"]
        assert state.approval["selected_option"] == expected["selected_option"]
        assert state.approval["command"] == (expected["command"] or "")
    else:
        assert state.reason_code == "needs-input"
        assert state.attention_kind == "question"
        assert state.attention_label == "ASK"
        assert state.approval["approval_visible"] is False
        assert state.approval["approval_type"] == ""
        assert state.approval["approval_action"] == ""


@pytest.mark.parametrize("case", PROMOTED_CAPTURE_CASES, ids=lambda case: case["id"])
def test_promoted_agent_tui_capture_names_include_client_version_and_date(case):
    path = case["path"]
    data = case["data"]
    inventory = case["inventory"]
    stem = path.stem
    version_slug = data["client_version_slug"]
    compact_date = data["capture_date"].replace("-", "")

    assert path.name == inventory["file"]
    assert data["fixture_id"] == stem
    assert inventory["id"] == stem
    assert "__" in stem
    assert version_slug in path.name
    assert compact_date in path.name
    assert data["fixture_scenario"] in path.name
    assert stem.endswith(f"_{compact_date}")
    assert re.fullmatch(r"[a-z0-9_]+__(claude-code|codex-cli)-\d+\.\d+\.\d+_\d{8}\.yaml", path.name)
    assert data["client_version"]
    assert data["capture_date"]
    if "source_staging_file" in inventory:
        assert data["source_staging_file"] == inventory["source_staging_file"]
    else:
        assert data["source"] == inventory["source"]
    assert data["failures"] == []


@pytest.mark.parametrize("case", PROMOTED_CAPTURE_CASES, ids=lambda case: case["id"])
def test_promoted_agent_tui_captures_reclassify_to_expected_state(case):
    data = case["data"]
    expected = data["expected_promoted"]
    cursor = cursor_from_capture(data)

    state = classify_agent_pane(
        {"pane_target": "%promoted-fixture", "agent_kind": data["agent"]},
        session="promoted-fixture",
        prompt_source="pane",
        include_composer=True,
        include_transcript_activity=False,
        capture_full_for_bash=False,
        capture_func=lambda _target, visible_only=False: data["raw_capture"],
        capture_styled_func=lambda _target, visible_only=False: data["styled_capture"],
        cursor_func=lambda _target: cursor,
    )

    assert state.agent_kind == expected["agent_kind"]
    assert state.screen["key"] == expected["screen_key"]
    assert state.reason_code == expected["reason_code"]
    assert state.attention_kind == expected["attention_kind"]
    assert state.attention_label == expected["attention_label"]
    assert state.approval["approval_visible"] is expected["approval_visible"]
    assert state.approval["approval_type"] == expected["approval_type"]
    assert state.composer.key == expected["composer_key"]


def test_codex_node_shell_approval_fixture_stays_yolo_approval():
    data = load_structured_fixture(PROMOTED_CAPTURE_DIR / "shell_approval_touch_command__codex-cli-0.141.0_20260620.yaml")
    cursor = cursor_from_capture(data)

    state = classify_agent_pane(
        {"pane_target": "%promoted-fixture", "agent_kind": data["agent"]},
        session="promoted-fixture",
        prompt_source="pane",
        include_composer=True,
        include_transcript_activity=False,
        capture_full_for_bash=False,
        capture_func=lambda _target, visible_only=False: data["raw_capture"],
        capture_styled_func=lambda _target, visible_only=False: data["styled_capture"],
        cursor_func=lambda _target: cursor,
    )

    assert data["agent"] == "codex"
    assert cursor.current_command == "node"
    assert state.reason_code == "approval"
    assert state.attention_label == "YOLO?"
    assert state.approval["approval_visible"] is True
    assert state.approval["approval_type"] == "bash"
    assert state.approval["selected_option"] == 1
    assert "touch /tmp/yolomux-codex-e2e-approval" in data["raw_capture"]


def test_send_prompt_clears_draft_before_paste_and_verifies_submit():
    draft = claude_composer("❯ old draft")
    empty = claude_composer("❯ ")
    captures = [draft, draft, empty, empty]
    calls = []

    def fake_capture(_target, visible_only=False):
        return captures.pop(0) if captures else empty

    def fake_clear(target):
        calls.append(("clear", target))
        return completed()

    def fake_paste(target, text, submit=False):
        calls.append(("paste", target, text, submit))
        return completed()

    result = send_prompt(
        {"pane_target": "%1", "agent_kind": "claude"},
        "new prompt",
        submit=True,
        clear_existing=True,
        verify_submit=True,
        clear_wait_seconds=0,
        verify_wait_seconds=0,
        capture_func=fake_capture,
        capture_styled_func=lambda _target, visible_only=False: "",
        clear_func=fake_clear,
        paste_func=fake_paste,
    )

    assert result.ok is True
    assert result.cleared is True
    assert calls == [("clear", "%1"), ("paste", "%1", "new prompt", True)]


def test_clear_composer_uses_cursor_to_clear_claude_nbsp_draft():
    draft = claude_composer("❯\xa0old draft")
    empty = claude_composer("❯ ")
    captures = [draft, empty]
    cursors = [AgentTuiCursor(x=11, y=31, character=" "), AgentTuiCursor(x=2, y=31, character=" ")]
    calls = []

    def fake_capture(_target, visible_only=False):
        return captures.pop(0) if captures else empty

    def fake_clear(target):
        calls.append(("clear", target))
        return completed()

    result = clear_composer(
        "%1",
        wait_seconds=0,
        capture_func=fake_capture,
        capture_styled_func=lambda _target, visible_only=False: "",
        cursor_func=lambda _target: cursors.pop(0) if cursors else AgentTuiCursor(x=2, y=31, character=" "),
        clear_func=fake_clear,
    )

    assert result.ok is True
    assert result.cleared is True
    assert result.detected_text == "old draft"
    assert calls == [("clear", "%1")]


def test_send_prompt_reports_unsubmitted_text_left_in_composer():
    visible = claude_composer("❯ new prompt")

    result = send_prompt(
        {"pane_target": "%1"},
        "new prompt",
        submit=True,
        preflight=False,
        clear_existing=False,
        verify_submit=True,
        verify_wait_seconds=0,
        capture_func=lambda _target, visible_only=False: visible,
        capture_styled_func=lambda _target, visible_only=False: "",
        paste_func=lambda _target, _text, submit=False: completed(),
    )

    assert result.ok is False
    assert result.sent is False
    assert result.pasted is True
    assert result.reason_code == "unsubmitted"


def test_send_prompt_preflight_refuses_non_agent_and_questions():
    non_agent = send_prompt(
        {"pane_target": "%1"},
        "new prompt",
        capture_func=lambda _target, visible_only=False: claude_composer("❯ "),
        capture_styled_func=lambda _target, visible_only=False: "",
    )
    question = send_prompt(
        {"pane_target": "%1", "agent_kind": "claude"},
        "new prompt",
        preflight_state=classify_agent_pane(
            {"pane_target": "%1", "agent_kind": "claude"},
            include_composer=False,
            include_transcript_activity=False,
            capture_func=lambda _target, visible_only=False: "Which backend?\n❯ 1. vLLM\n  2. SGLang",
            capture_styled_func=lambda _target, visible_only=False: "",
            prompt_classifier=lambda _target, _visible, _pane, _source: {"visible": False},
            screen_classifier=lambda _visible, _pane_target: {"key": "needs-input", "text": "Which backend?"},
        ),
    )

    assert non_agent.reason_code == "not-agent"
    assert non_agent.sent is False
    assert question.reason_code == "needs-input"
    assert question.sent is False
