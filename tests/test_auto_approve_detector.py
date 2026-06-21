import argparse
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


import auto_approve_tmux
from yolomux_lib import approvals
from yolomux_lib import prompt_detector
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import SessionInfo


PROMPT_CORPUS_DIR = Path(__file__).resolve().parent / "fixtures" / "prompt_corpus"


def load_structured_fixture(path: Path):
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


def fixture_visible_text(path: Path) -> str:
    if path.suffix in {".json", ".yaml", ".yml"}:
        data = load_structured_fixture(path)
        return str(data.get("raw_capture") or data.get("visible_text") or "")
    return path.read_text(encoding="utf-8")


def load_prompt_corpus():
    inventory = load_structured_fixture(PROMPT_CORPUS_DIR / "inventory.yaml")
    cases = []
    for fixture in inventory["fixtures"]:
        case = dict(fixture)
        case["text"] = fixture_visible_text(PROMPT_CORPUS_DIR / fixture["file"])
        cases.append(case)
    return inventory, cases


PROMPT_CORPUS_INVENTORY, PROMPT_CORPUS_CASES = load_prompt_corpus()


def load_capture_harness_module():
    path = PROMPT_CORPUS_DIR / "capture_prompt_fixture.py"
    spec = importlib.util.spec_from_file_location("capture_prompt_fixture_for_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def option_labels(options):
    return [str(option["label"]) for option in options]


def claude_bash_prompt_with_footer(*footer_lines):
    return "\n".join([
        "Bash command (unsandboxed)",
        "",
        "   echo one",
        "   Pause before continuing",
        "",
        " Permission rule Bash requires confirmation for this command.",
        "",
        " Do you want to proceed?",
        " ❯ 1. Yes",
        "   2. No",
        "",
        *footer_lines,
    ])


def test_auto_approve_tmux_reexports_detector_helpers():
    assert auto_approve_tmux.detect_prompt is prompt_detector.detect_prompt
    assert auto_approve_tmux.extract_command is prompt_detector.extract_command
    assert auto_approve_tmux.approval_prompt_state is prompt_detector.approval_prompt_state
    assert auto_approve_tmux.prompt_hash is prompt_detector.prompt_hash


def test_approval_detection_pipeline_has_one_shared_owner():
    # The read-path (app.py), the act-path worker (via the auto_approve_tmux module seam), and the CLI
    # must all run the SAME detection functions — no parallel re-implementation. Detection lives in
    # yolomux_lib.approvals; the root CLI re-exports it and app.py imports it directly. Identity here
    # means a future divergent copy fails this test instead of silently drifting the two paths apart.
    from yolomux_lib import app as app_module
    from yolomux_lib import approvals

    assert auto_approve_tmux.hybrid_approval_prompt_state is approvals.hybrid_approval_prompt_state
    assert auto_approve_tmux.transcript_approval_prompt_state is approvals.transcript_approval_prompt_state
    assert auto_approve_tmux.blank_prompt_state is approvals.blank_prompt_state
    assert auto_approve_tmux.PROMPT_RETRY_SECONDS == approvals.PROMPT_RETRY_SECONDS
    assert app_module.hybrid_approval_prompt_state is approvals.hybrid_approval_prompt_state
    assert app_module.blank_prompt_state is approvals.blank_prompt_state


def test_auto_approve_cli_state_keeps_normal_questions_out_of_approval_path():
    visible_text = "Which backend should I use?\n❯ 1. vLLM\n  2. SGLang"

    state, pane_text = auto_approve_tmux.classify_auto_approve_state("6", visible_text, prompt_source="pane")

    assert pane_text is None
    assert state.reason_code == "needs-input"
    assert state.attention_kind == "question"
    assert state.display["attention_label"] == "ASK?"
    assert state.approval["approval_visible"] is False


def test_auto_approve_cli_once_sends_from_central_approval_state(monkeypatch):
    sent = []
    state = SimpleNamespace(
        prompt={
            "visible": True,
            "type": "bash",
            "yes_selected": True,
            "selected_option": 1,
            "hash": "hash-cli",
            "source": "pane",
            "command": "make test",
        },
        approval={
            "approval_visible": True,
            "approval_type": "bash",
            "approval_action": "option1",
            "selected_option": 1,
            "command": "make test",
            "prompt_hash": "hash-cli",
            "source": "pane",
        },
        screen={"key": "approval", "text": "Do you want to proceed?"},
        reason_code="approval",
    )

    monkeypatch.setattr(auto_approve_tmux.sys, "argv", ["auto_approve_tmux.py", "--once", "6"])
    monkeypatch.setattr(auto_approve_tmux, "resolve_targets", lambda _targets: ["6"])
    monkeypatch.setattr(auto_approve_tmux, "tmux_has_session", lambda _session: True)
    monkeypatch.setattr(auto_approve_tmux, "tmux_capture_pane", lambda _target, visible_only=False: "visible approval")
    monkeypatch.setattr(auto_approve_tmux, "classify_auto_approve_state", lambda _target, _visible, prompt_source="hybrid": (state, "make test"))
    monkeypatch.setattr(auto_approve_tmux, "standalone_bash_decision", lambda *_args, **_kwargs: {"action": "approve"})
    monkeypatch.setattr(auto_approve_tmux, "tmux_send_option", lambda target, option, selected_option=None: sent.append((target, option, selected_option)))
    monkeypatch.setattr(auto_approve_tmux.time, "sleep", lambda *_args: None)

    auto_approve_tmux.main()

    assert sent == [("6", 1, 1)]


def test_capture_prompt_fixture_harness_contract(monkeypatch, tmp_path):
    harness = load_capture_harness_module()

    assert harness.parse_size("120x40") == (120, 40)
    with pytest.raises(argparse.ArgumentTypeError):
        harness.parse_size("30x9")
    paths = harness.fixture_paths(tmp_path, "codex_sleep", [(80, 24), (120, 40)], version_slug="codex-cli-0.141.0", capture_date="20260620")
    assert [capture.name for _size, capture in paths] == ["codex_sleep_80x24__codex-cli-0.141.0_20260620.yaml", "codex_sleep_120x40__codex-cli-0.141.0_20260620.yaml"]
    sanitized = harness.sanitize_text(f"{Path.home()}/repo sk-abcdefghijklmnop user@example.com\n")
    assert "~/repo" in sanitized
    assert "<redacted-token>" in sanitized
    assert "<redacted-email>" in sanitized

    tmux_calls = []

    def fake_run_tmux(socket, args, timeout=10):
        tmux_calls.append((socket, args, timeout))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(harness, "run_tmux", fake_run_tmux)
    target, launched_session = harness.launched_target(
        argparse.Namespace(
            launch_command="",
            launch_agent="codex",
            target=None,
            session="capture-codex",
            socket="/tmp/yolomux-capture.sock",
        ),
        "codex_sleep",
        (100, 30),
    )
    assert target == "capture-codex:"
    assert launched_session == "capture-codex"
    assert tmux_calls == [("/tmp/yolomux-capture.sock", ["new-session", "-d", "-s", "capture-codex", "-x", "100", "-y", "30", "codex"], 10)]

    waits = []
    tmux_calls.clear()

    def fake_wait_for_text(target, needles, timeout, include_scrollback, socket=""):
        waits.append((target, tuple(needles), timeout, include_scrollback, socket))
        return "ready"

    monkeypatch.setattr(harness, "wait_for_text", fake_wait_for_text)
    harness.drive_prompt(
        argparse.Namespace(
            ready_text=["›"],
            send_line=["sleep 10"],
            wait_text=["Would you like to run the following command?"],
            timeout=12,
            include_scrollback=False,
            socket="/tmp/yolomux-capture.sock",
        ),
        "capture-codex:",
    )
    assert waits == [
        ("capture-codex:", ("›",), 12, False, "/tmp/yolomux-capture.sock"),
        ("capture-codex:", ("Would you like to run the following command?",), 12, False, "/tmp/yolomux-capture.sock"),
    ]
    assert tmux_calls == [("/tmp/yolomux-capture.sock", ["send-keys", "-t", "capture-codex:", "sleep 10", "Enter"], 10)]


def test_standalone_bash_decision_fails_closed_without_extracted_command(monkeypatch):
    def unexpected_evaluate(*_args, **_kwargs):
        raise AssertionError("missing commands must not fall through to rule evaluation")

    monkeypatch.setattr(auto_approve_tmux.yolo_rules, "evaluate", unexpected_evaluate)

    decision = auto_approve_tmux.standalone_bash_decision(None, "6")

    assert decision["action"] == "ask"
    assert decision["command_missing"] is True
    assert decision["rule_name"] == "command extraction failed"


def test_standalone_bash_decision_uses_yolo_rule_engine(monkeypatch):
    calls = []

    def fake_evaluate(cmd, prompt_type="bash", agent="", session="", dangerously_yolo=False):
        calls.append((cmd, prompt_type, agent, session, dangerously_yolo))
        return {
            "action": "notify",
            "rule_name": "custom default",
            "risk": "unknown",
            "source": "file",
            "path": "/tmp/yolo-rules.yaml",
        }

    monkeypatch.setattr(auto_approve_tmux.yolo_rules, "evaluate", fake_evaluate)

    decision = auto_approve_tmux.standalone_bash_decision("python3 script.py", "6")

    assert calls == [("python3 script.py", "bash", "", "6", False)]
    assert decision["action"] == "notify"
    assert decision["rule_name"] == "custom default"
    assert decision["command_missing"] is False


def test_hybrid_approval_prompt_state_uses_recent_transcript_when_pane_header_is_missing(monkeypatch, tmp_path):
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "make test"}}],
            },
        }) + "\n",
        encoding="utf-8",
    )
    info = SessionInfo(
        session="6",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="6",
                kind="claude",
                pid=123,
                pane_target="6:0.0",
                command="claude",
                cwd=None,
                status=None,
                session_id="session-6",
                transcript=str(transcript),
                error=None,
            )
        ],
    )
    monkeypatch.setattr(approvals, "discover_sessions", lambda sessions: ({"6": info}, []))

    state = auto_approve_tmux.hybrid_approval_prompt_state("6", "❯ 1. Yes\n  2. No")

    assert state["visible"] is True
    assert state["source"] == "transcript"
    assert state["type"] == "bash"
    assert state["command"] == "make test"
    assert state["selected_option"] == 1


def test_hybrid_approval_prompt_state_does_not_use_transcript_without_visible_selector(monkeypatch, tmp_path):
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "make test"}}],
            },
        }) + "\n",
        encoding="utf-8",
    )
    info = SessionInfo(
        session="6",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="6",
                kind="claude",
                pid=123,
                pane_target="6:0.0",
                command="claude",
                cwd=None,
                status=None,
                session_id="session-6",
                transcript=str(transcript),
                error=None,
            )
        ],
    )
    monkeypatch.setattr(approvals, "discover_sessions", lambda sessions: ({"6": info}, []))

    state = auto_approve_tmux.hybrid_approval_prompt_state("6", "agent is thinking")

    assert state["visible"] is False
    assert state["type"] == ""
    assert "no visible selectable prompt" in state["reason"]


def test_extract_command_rejoins_wrapped_codex_command():
    visible_text = "\n".join([
        "  Would you like to run the following command?",
        "",
        "  Reason: Verify the persisted YOLOmux settings file now defaults",
        "  the editor font size to 13.",
        "",
        '  $ python3 -c "from yolomux_lib.settings import settings_payload;',
        '  print(settings_payload()[\'settings\'][\'appearance\'][\'editor_font_size\'])"',
        "",
        "› 1. Yes, proceed (y)",
        "  2. No, and tell Codex what to do differently (esc)",
        "",
        "  Press enter to confirm or esc to cancel",
    ])

    assert prompt_detector.extract_command(visible_text) == (
        'python3 -c "from yolomux_lib.settings import settings_payload; '
        'print(settings_payload()[\'settings\'][\'appearance\'][\'editor_font_size\'])"'
    )


def test_extract_command_does_not_truncate_at_a_safe_complete_prefix():
    # #61: `git push origin main` parses shlex-complete, but the dangerous tail wraps onto the next
    # visual line — the FULL joined command must be classified, not the safe-looking prefix.
    visible_text = "\n".join([
        "  Would you like to run the following command?",
        "  Reason: push the branch",
        "  $ git push origin main",
        "    --force-with-lease --no-verify",
        "› 1. Yes, proceed (y)",
        "  2. No, and tell Codex what to do differently (esc)",
        "  Press enter to confirm or esc to cancel",
    ])
    assert prompt_detector.extract_command(visible_text) == "git push origin main --force-with-lease --no-verify"


def test_extract_command_returns_none_when_codex_block_has_no_selector():
    # #61: a capture that ends WITHOUT the selector may be truncated mid-command, so it is treated as
    # incomplete (None) and the caller falls to `ask` instead of trusting a prefix.
    visible_text = "\n".join([
        "  Would you like to run the following command?",
        "  Reason: clean up",
        "  $ rm -rf /tmp/build && curl http://example",
    ])
    assert prompt_detector.extract_command(visible_text) is None


def test_extract_command_anchors_to_bash_call_arg_not_prose():
    # anchor to the ● Bash(...) arg — never fold the description prose into the command.
    visible_text = "\n".join([
        "● Bash(git status --short)",
        "  Show the working tree status so we can decide what to commit next and keep the repo tidy",
        " Permission rule Bash requires confirmation for this command.",
        " Do you want to proceed?",
        " ❯ 1. Yes",
        "   2. No",
    ])
    assert prompt_detector.extract_command(visible_text) == "git status --short"


def test_extract_command_does_not_cross_separator_into_prior_step():
    # the LIVE Claude prompt shows the command in the box with NO `● Bash()` (that renders only
    # after approval). The `● Bash()` anchor search must be bounded to the current block so it does not
    # walk past the `─────` separator and return the PREVIOUS step's (stale, safe-looking) command.
    visible_text = "\n".join([
        "● Bash(chmod +x scripts/deploy.sh)",
        "  ⎿  ok",
        "● Done.",
        "──────────────────────────────────────────────",
        " Bash command (unsandboxed)",
        "",
        "   cp -r src/ dist/",
        "   [3/10] Copy sources into dist/",
        "",
        " Permission rule Bash requires confirmation for this command.",
        " Do you want to proceed?",
        " ❯ 1. Yes",
        "   2. No",
    ])
    cmd = prompt_detector.extract_command(visible_text)
    assert cmd is not None and cmd.startswith("cp -r src/ dist/")
    assert "chmod" not in cmd


def test_extract_command_does_not_fold_long_description_prose():
    # a long description line (no shell metacharacters) is NOT folded into the command.
    visible_text = "\n".join([
        "Bash command",
        "",
        "   rm -rf /tmp/build",
        "   This removes the build directory and triggers a clean rebuild from scratch every time now",
        "",
        " Permission rule Bash requires confirmation for this command.",
        " Do you want to proceed?",
        " ❯ 1. Yes",
    ])
    assert prompt_detector.extract_command(visible_text) == "rm -rf /tmp/build"


# Fixtures transcribed verbatim from DOIT.3.md "Captured shapes" — the EXACT mock renders
# (mock Claude 2-option, mock Codex 3-option), including the leading `● Mock build script` status line,
# the box-border `────` rule, the `›` (U+203A) selector glyph, and the `[i/N]` step marker. These pin
# the auto-approve contract so a future mock-UI change fails the test instead of silently breaking it.
MOCK_CLAUDE_YESNO_PROMPT = "\n".join([
    "● Mock build script — 10 steps, each needs Yes/No.",
    "────────────────────────────",
    " Bash command (unsandboxed)",
    "",
    "   mkdir -p build/output",
    "   [1/10] Create the build output directory",
    "",
    " Permission rule Bash requires confirmation for this command.",
    "",
    " Do you want to proceed?",
    " ❯ 1. Yes",
    "   2. No",
    "",
    " Esc to cancel",
])

MOCK_CODEX_YESNO_PROMPT = "\n".join([
    "● Mock build script — 10 steps, each needs Yes/No.",
    "────────────────────────────",
    "◦ Running mkdir -p build/output",
    "",
    "",
    "  Would you like to run the following command?",
    "",
    "  $ mkdir -p build/output",
    "",
    "› 1. Yes, proceed (y)",
    "  2. Yes, and don't ask again for commands that start with `mkdir -p` (p)",
    "  3. No, and tell Codex what to do differently (esc)",
    "",
    "  Press enter to confirm or esc to cancel",
])


def test_prompt_corpus_inventory_declares_required_families():
    fixture_families = {case["family"] for case in PROMPT_CORPUS_CASES}
    gap_families = {gap["family"] for gap in PROMPT_CORPUS_INVENTORY["gaps"]}

    assert set(PROMPT_CORPUS_INVENTORY["required_families"]) <= fixture_families | gap_families
    assert {source["url"] for source in PROMPT_CORPUS_INVENTORY["sources"]}
    assert (PROMPT_CORPUS_DIR / PROMPT_CORPUS_INVENTORY["capture_harness"]).exists()


@pytest.mark.parametrize("case", PROMPT_CORPUS_CASES, ids=lambda case: case["id"])
def test_prompt_corpus_cases_classify_with_structured_evidence(case):
    text = case["text"]
    expected = case["expected"]

    prompt = prompt_detector.approval_prompt_state(text, text)
    screen = prompt_detector.agent_screen_state(text)

    assert prompt["visible"] is expected["approval_visible"]
    assert screen["key"] == expected["screen_key"]

    if expected["ask"]:
        payload = prompt if expected["approval_visible"] else screen
        assert payload["agent"] == expected["agent"]
        assert payload["prompt_kind"] == expected["prompt_kind"]
        assert payload["question_text"] == expected["question_text"]
        assert payload["command"] == expected["command"]
        assert payload["selected_option"] == expected["selected_option"]
        assert option_labels(payload["options"]) == expected["option_labels"]
        assert float(payload["confidence"]) >= expected["confidence_min"]
        assert payload["evidence_lines"], case["id"]
        assert payload.get("hash") or payload.get("prompt_hash")

        if expected["approval_visible"]:
            assert prompt["type"] == expected["prompt_type"]
            assert prompt["yes_selected"] is expected["yes_selected"]
            assert prompt["action"] == expected["action"]
            assert prompt["negative_reason"] == ""
        else:
            assert prompt["type"] == ""
            assert prompt["action"] is None
        return

    assert prompt["visible"] is False
    assert prompt["action"] is None
    assert prompt["command"] is None
    assert prompt["options"] == []
    negative_reason = str(prompt.get("negative_reason") or screen.get("negative_reason") or "")
    assert expected["negative_reason_contains"] in negative_reason


def test_mock_claude_yesno_extract_command_drops_step_marker():
    # Y3: the `[1/10] <description>` line is DESCRIPTION prose, not the command. Before the fix the `/`
    # in `[1/10]` matched _CMD_CHARS and the whole line folded into the command
    # ("mkdir -p build/output [1/10] Create the build output directory"), skewing classification.
    assert prompt_detector.extract_command(MOCK_CLAUDE_YESNO_PROMPT) == "mkdir -p build/output"


def test_mock_claude_yesno_approve_targets_option_1():
    # Y5/Y6: the mock Claude yesno render is a live bash prompt with `1. Yes` selected by `›`.
    state = prompt_detector.approval_prompt_state(MOCK_CLAUDE_YESNO_PROMPT, MOCK_CLAUDE_YESNO_PROMPT)
    assert state["visible"] is True
    assert state["type"] == "bash"
    assert state["yes_selected"] is True
    assert state["selected_option"] == 1
    assert state["action"] == "option1"
    assert state["command"] == "mkdir -p build/output"


def test_mock_codex_yesno_extract_command_drops_reason_and_step_marker():
    # Y8: the `$ `-prefixed command is the only command; the `◦ Running ...` line is chrome.
    assert prompt_detector.extract_command(MOCK_CODEX_YESNO_PROMPT) == "mkdir -p build/output"


def test_mock_codex_3_option_approve_targets_option_1_yes():
    # Y8: Codex offers `1. Yes, proceed` / `2. Yes, and don't ask again ...` / `3. No ...`. `No` is option 3, NOT 2.
    # The approve action MUST target the `›`-selected `1. Yes` and never assume "2 == No" or exactly two
    # options. The option-2 "don't ask again" prefix (`mkdir -p`) is NOT a generic recurring prefix, so
    # it stays at option 1.
    state = prompt_detector.approval_prompt_state(MOCK_CODEX_YESNO_PROMPT, MOCK_CODEX_YESNO_PROMPT)
    assert state["visible"] is True
    assert state["type"] == "bash"
    assert state["yes_selected"] is True
    assert state["selected_option"] == 1
    assert state["action"] == "option1"
    assert prompt_detector.action_for_bash_prompt(MOCK_CODEX_YESNO_PROMPT) == "option1"


def test_mock_codex_3_option_worker_walks_to_option_1():
    # Y8 act-path: the worker derives the target option from the approve action and walks the highlight
    # to `1. Yes` (not `2`, not `3. No`), re-verifies it landed on 1, then confirms. Drives the worker's
    # send_action against a fake tmux module seam so the chosen option is observable without a live tmux.
    import types

    recorded: dict[str, object] = {}

    def fake_move(target, option, selected_option=None):
        recorded["moved_to"] = option

    def fake_send_enter(target):
        recorded["sent_enter"] = True

    module = types.SimpleNamespace(
        tmux_capture_pane=lambda target, visible_only=False: MOCK_CODEX_YESNO_PROMPT,
        selected_prompt_option=prompt_detector.selected_prompt_option,
        extract_command=prompt_detector.extract_command,
        PROMPT_RETRY_SECONDS=8.0,
        tmux_move_to_option=fake_move,
        tmux_send_enter=fake_send_enter,
    )

    from yolomux_lib.auto_approve_worker import AutoApproveWorker

    worker = AutoApproveWorker("codex-test")
    action = prompt_detector.action_for_bash_prompt(MOCK_CODEX_YESNO_PROMPT)
    assert worker.send_action(module, action, selected_option=1) is True
    assert recorded == {"moved_to": 1, "sent_enter": True}


@pytest.mark.parametrize("glyph", ["❯", "›", ">"])
@pytest.mark.parametrize("prompt", [MOCK_CLAUDE_YESNO_PROMPT, MOCK_CODEX_YESNO_PROMPT])
def test_selector_glyph_agnostic_detection(prompt, glyph):
    # Detection must NOT depend on which selector glyph the agent renders. Real Claude uses ❯, real
    # Codex uses ›, and `>` is the plain-ASCII fallback — all three must resolve identically to the
    # `1. Yes` option with the bare command. (The mock glyph is cosmetic; this is the durable guarantee.)
    text = prompt.replace("❯", glyph).replace("›", glyph)
    assert prompt_detector.detect_prompt(text) == "bash"
    assert prompt_detector.action_for_bash_prompt(text) == "option1"
    assert prompt_detector.selected_prompt_option(text) == 1
    assert prompt_detector.extract_command(text) == "mkdir -p build/output"


def test_approval_prompt_ignores_exact_claude_ctrl_b_footer():
    visible_text = claude_bash_prompt_with_footer(
        " Esc to cancel · Tab to amend · ctrl+e to explain",
        " (ctrl+b to run in background)",
    )

    assert prompt_detector.approval_prompt_has_later_activity(visible_text) is False
    state = prompt_detector.approval_prompt_state(visible_text)
    assert state["visible"] is True
    assert state["type"] == "bash"


def test_approval_prompt_ignores_multi_key_parenthetical_footer():
    # a footer hint with one-or-more keys plus a parenthetical — e.g.
    # "(ctrl+b ctrl+b (twice) to run in background)" — must read as a footer (not later activity), so the
    # live approval prompt is still detected and auto-approvable.
    visible_text = claude_bash_prompt_with_footer(
        " Esc to cancel · Tab to amend · ctrl+e to explain",
        " (ctrl+b ctrl+b (twice) to run in background)",
    )
    assert prompt_detector.approval_prompt_has_later_activity(visible_text) is False
    state = prompt_detector.agent_screen_state(visible_text)
    assert state["key"] == "approval"
    assert "Do you want to proceed?" in state["text"]
    # Sanity: a real command sentence ending in "to <verb>" is NOT swallowed as a footer.
    assert prompt_detector._FOOTER_HINT_PART_RE.match("rm -rf / to delete everything") is None


def test_approval_prompt_ignores_dot_separated_ctrl_hint_cluster():
    visible_text = claude_bash_prompt_with_footer(
        " Esc to cancel · Tab to amend · ctrl+e to explain",
        " ctrl+b to run in background. ctrl+t to hide tasks",
    )

    assert prompt_detector.approval_prompt_has_later_activity(visible_text) is False
    assert prompt_detector.approval_prompt_state(visible_text)["visible"] is True


def test_approval_prompt_fires_with_ctrl_t_task_list_below_prompt():
    # Ctrl-T renders the todo overlay BELOW the prompt footer (header + items + "+N pending" + boxed
    # input). The whole block is chrome under a LIVE prompt; it must not read as "later activity" or
    # the prompt looks dismissed and auto-approve never fires (image 090).
    visible_text = claude_bash_prompt_with_footer(
        " Esc to cancel · Tab to amend · ctrl+e to explain",
        " ctrl+b to run in background · ctrl+t to hide tasks",
        " 11 tasks (0 done, 1 in progress, 10 open)",
        "   First task description that wraps onto",
        "   a second continuation line",
        "   Second task",
        "   +6 pending",
        " │ > │",
    )

    assert prompt_detector.approval_prompt_has_later_activity(visible_text) is False
    assert prompt_detector.detect_prompt(visible_text) == "bash"
    assert prompt_detector.selected_prompt_option(visible_text) == 1
    state = prompt_detector.approval_prompt_state(visible_text, visible_text)
    assert state["visible"] is True
    assert state["type"] == "bash"
    assert state["yes_selected"] is True
    assert state["action"] == "option1"


def test_task_list_header_break_does_not_mask_real_dismissal_above_it():
    # REGRESSION GUARD: genuine agent output (● bullet / ⎿ result) ABOVE a later task list must still
    # mark the prompt dismissed — the header break only short-circuits chrome between footer and header.
    visible_text = "\n".join([
        " Do you want to proceed?",
        " ❯ 1. Yes",
        "   2. No",
        " Esc to cancel · Tab to amend · ctrl+e to explain",
        " ● Ran the command and moved on",
        "   ⎿  output line",
        " 3 tasks (1 done, 0 in progress, 2 open)",
        "   Later task",
    ])

    assert prompt_detector.approval_prompt_has_later_activity(visible_text) is True
    assert prompt_detector.approval_prompt_state(visible_text)["visible"] is False


def test_claude_no_caret_prompt_is_not_treated_as_selected():
    # a prompt with NO selector glyph (nothing highlighted — e.g. a redraw frame) must NOT
    # be auto-confirmed from a positional "option 1 is Yes" guess. A send requires a visible ❯/›/box.
    visible_text = claude_bash_prompt_with_footer(
        " Esc to cancel · Tab to amend · ctrl+e to explain",
    ).replace(" ❯ 1. Yes", "   1. Yes")

    assert prompt_detector.approval_prompt_has_later_activity(visible_text) is False
    assert prompt_detector.yes_is_selected(visible_text) is False
    assert prompt_detector.selected_prompt_option(visible_text) == 0
    assert prompt_detector.approval_prompt_state(visible_text)["yes_selected"] is False


def test_claude_no_caret_prompt_does_not_default_when_stale():
    visible_text = "\n".join([
        claude_bash_prompt_with_footer(
            " Esc to cancel · Tab to amend · ctrl+e to explain",
        ).replace(" ❯ 1. Yes", "   1. Yes"),
        "● User approved Claude's request",
        "● Bash(echo one)",
        "  ⎿  ok",
        "",
        "❯ ",
    ])

    assert prompt_detector.approval_prompt_has_later_activity(visible_text) is True
    assert prompt_detector.yes_is_selected(visible_text) is False
    assert prompt_detector.approval_prompt_state(visible_text)["visible"] is False


def test_approval_prompt_detects_activity_after_claude_ctrl_b_footer():
    visible_text = "\n".join([
        claude_bash_prompt_with_footer(
            " Esc to cancel · Tab to amend · ctrl+e to explain",
            " (ctrl+b to run in background)",
        ),
        "● User approved Claude's request",
        "● Bash(echo one)",
        "  ⎿  ok",
        "",
        "❯ ",
    ])

    assert prompt_detector.approval_prompt_has_later_activity(visible_text) is True
    assert prompt_detector.approval_prompt_state(visible_text)["visible"] is False


VISIBLE_AGENT_WORKING_CASES = [
    pytest.param("◦ Working (1m 21s • esc to interrupt)\n", True, "working", id="codex-circle-working-footer"),
    pytest.param("• Working (6m 38s • esc to interrupt)\n", True, "working", id="codex-bullet-working-footer"),
    pytest.param("◦ Reviewing files (24s • esc to interrupt)\n", True, "working", id="codex-esc-to-interrupt-no-working-word"),
    pytest.param(
        "\n".join([
            "○ Working (4m 09s • esc to interrupt)",
            "",
            "› Implement {feature}",
            "",
            "  gpt-5.5 xhigh · ~",
        ]),
        True,
        "working",
        id="codex-working-with-input-prompt-below",
    ),
    pytest.param(
        "\n".join([
            "○ Working (33m 05s · esc to interrupt)",
            "╭────────────────────────────────────────────╮",
            "│ Use /skills to list available skills       │",
            "│ >                                          │",
            "╰────────────────────────────────────────────╯",
            "gpt-5.5 xhigh   ~/yolomux.dev2",
        ]),
        True,
        "working",
        id="codex-working-with-bottom-composer-and-model-status",
    ),
    pytest.param(
        "✱ Imagining… (4s · ↓ 98 tokens)\n  ⎿  Tip: Connect Claude to your IDE · /ide\n",
        True,
        "working",
        id="claude-random-status-imagining",
    ),
    pytest.param("✦ Comboublahblah… (7s · ↓ 123 tokens)\n", True, "working", id="claude-random-status-combo"),
    pytest.param("✳ Doodooshit… (1m 2s · ↓ 1.2k tokens)\n", True, "working", id="claude-random-status-doodoo"),
    pytest.param("☉ Refactoring... (2.3s · ↑ 13 tokens · high effort)\n", True, "working", id="claude-random-status-refactor"),
    pytest.param(
        "\n".join([
            "✶ Thinking… (1s · ↑ 26.9k tokens · esc to interrupt)",
            "100% context used",
            "▶▶ bypass permissions on · 1 shell · esc to interrupt",
        ]),
        True,
        "working",
        id="claude-context-used-status-line",
    ),
    pytest.param(
        "\n".join([
            "⠿ Running 2 agents…",
            "  ├ Verify detector fixtures · 14 tool uses · 31.2k tokens",
            "  └ Check current Claude pane state · 23 tool uses · 77.5k tokens",
            "",
            "(ctrl+b to run in background)",
        ]),
        True,
        "working",
        id="claude-multi-agent-header",
    ),
    pytest.param(
        "\n".join([
            "● Lollygagging… (2m 1s · ↓ 8.0k tokens · thinking with xhigh effort)",
            "",
            "╭────────────────────────────────────────────╮",
            "│ >                                          │",
            "╰────────────────────────────────────────────╯",
            "⏺ xhigh /effort",
            "▶▶ bypass permissions on · 1 shell · esc to interrupt",
        ]),
        True,
        "working",
        id="claude-boxed-input-chrome-below-spinner",
    ),
    pytest.param(
        "\n".join([
            "● Updated 3 files and finished the task.",
            "",
            "╭────────────────────────────────────────────╮",
            "│ >                                          │",
            "╰────────────────────────────────────────────╯",
            "▶▶ bypass permissions on · 1 shell",
        ]),
        False,
        None,
        id="finished-agent-without-live-spinner",
    ),
    pytest.param(
        "\n".join([
            "● Honking… (1m 12s · ↓ 5.8k tokens)",
            "",
            "● main  Fix preferences focus  ↑/↓ to select · Enter to view",
            "○ Explore  Check current pane  47s",
            "● xhigh /effort",
        ]),
        True,
        "working",
        id="claude-work-queue-below-spinner",
    ),
    pytest.param(
        "\n".join([
            "╭────────────────────────────────────────────╮",
            "│ >                                          │",
            "╰────────────────────────────────────────────╯",
            "⏺ xhigh /effort",
        ]),
        False,
        "idle",
        id="boxed-input-chrome-without-working-line",
    ),
    pytest.param(
        "\n".join([
            "  Then sleep 10 approval should show:",
            "",
            "  • Working (10s • esc to interrupt)",
            "",
            "  with Working animated in the real TTY.",
            "",
            "› Explain this codebase",
            "",
            "  gpt-5.5 xhigh · ~",
        ]),
        False,
        "idle",
        id="stale-example-above-prompt",
    ),
    pytest.param("  esc to interrupt · ctrl+t to hide tasks\n", False, None, id="non-parenthesized-footer-hint"),
    pytest.param(
        "◦ Working (1m 21s • esc to interrupt)\n"
        "  □ Pending task one\n"
        "  ✓ Done task two\n"
        "  ✗ Failed task three\n"
        "  ◯ Another pending\n",
        True,
        "working",
        id="unicode-task-glyphs",
    ),
    pytest.param(
        "○ Working (4m 09s • esc to interrupt)\nuser@host$ echo done\n",
        False,
        None,
        id="working-line-with-real-shell-prompt-below-is-stale",
    ),
    pytest.param(
        "\n".join([
            "Working (12m 56s · esc to interrupt)",
            "",
            "› Write tests for @filename",
            "",
            "  gpt-5.5 xhigh · ~/yolomux.dev1 Goal achieved (42.1k tokens)",
        ]),
        True,
        "working",
        id="codex-bare-working-row-above-composer",
    ),
    pytest.param(
        "\n".join([
            "✽ Tomfoolering… (7m 12s · ↓ 30.1k tokens · almost done thinking with xhigh effort)",
            "Tip: Connect Claude to your IDE · /ide",
            "──────────────────────────────────────────────",
            "╭────────────────────────────────────────────╮",
            "│ >                                          │",
            "╰────────────────────────────────────────────╯",
            "⏺ xhigh /effort",
        ]),
        True,
        "working",
        id="claude-counter-tip-and-idle-composer",
    ),
    pytest.param(
        "\n".join([
            ". Tomfoolering… (10m 35s · ↓ 55.4K tokens)",
            "Tip: /ultrareview runs a deep, multi-agent review of your changes",
            "──────────────────────────────────────────────",
            "╭────────────────────────────────────────────╮",
            "│ >                                          │",
            "╰────────────────────────────────────────────╯",
            "▶▶ auto mode on · ctrl+b ctrl+b (twice) to run in background",
            "○general-purpose Audit yoagent streaming DOIT 2m 30s · ↓ 59.7K tokens",
        ]),
        True,
        "working",
        id="claude-dot-counter-footer-and-background-agent",
    ),
    pytest.param(
        "\n".join([
            "· Tomfoolering… (12m 49s · ↑ 64.3k tokens)",
            "Tip: /ultrareview runs a deep, multi-agent review of your changes — available in Claude for Enterprise · Learn more",
            "╭────────────────────────────────────────────╮",
            "│ >                                          │",
            "╰────────────────────────────────────────────╯",
        ]),
        True,
        "working",
        id="claude-middle-dot-up-token-tip",
    ),
    pytest.param(
        "\n".join([
            "✱ Tomfoolering... (6m 34s · ↓ 30.1k tokens · still thinking with xhigh effort)",
            "✳ Wobbleflorping… (2m 01s · ↓ 8.4K tokens)",
            "☉ Any status words here... (24s · ↑ 13 tokens · high effort)",
        ]),
        True,
        "working",
        id="claude-arbitrary-status-counter-text",
    ),
]


@pytest.mark.parametrize("visible_text, expected_working, expected_key", VISIBLE_AGENT_WORKING_CASES)
def test_visible_agent_working_cases(visible_text, expected_working, expected_key):
    assert prompt_detector.visible_agent_working(visible_text) is expected_working
    if expected_key is not None:
        assert prompt_detector.agent_screen_state(visible_text)["key"] == expected_key


@pytest.mark.parametrize(
    "line, elapsed, tokens",
    [
        ("✽ Tomfoolering… (7m 12s · ↓ 30.1k tokens · almost done thinking with xhigh effort)", 432, 30100),
        ("☉ Any status words here... (24s · ↑ 13 tokens · high effort)", 24, 13),
        ("☉ Decimal seconds... (2.3s · ↑ 13 tokens · high effort)", 2.3, 13),
        ("✳ Wobbleflorping… (1h 02m 03s · ↓ 8.4K tokens)", 3723, 8400),
        ("✳ Wobbleflorping… (3h 45m · ↓ 8.4K tokens)", 13500, 8400),
        ("✳ Wobbleflorping… (3h · ↓ 8.4K tokens)", 10800, 8400),
        ("✱ No tokens... (6m 34s · still thinking with xhigh effort)", 394, None),
        ("✱ Big tokens... (6m 34s · ↓ 1.2M tokens)", 394, 1200000),
    ],
)
def test_parse_agent_status_counter_durations_and_tokens(line, elapsed, tokens):
    counter = prompt_detector.parse_agent_status_counter(line)

    assert counter is not None
    assert counter["status_elapsed_seconds"] == elapsed
    assert counter["status_tokens"] == tokens
    assert counter["status_line"] == line


def test_parse_claude_background_agent_status_counter():
    line = "○general-purpose Audit ui_tree followups DOIT 2m 15s · ↓ 69.1K tokens"

    counter = prompt_detector.parse_agent_status_counter(line)

    assert counter is not None
    assert counter["status_elapsed_seconds"] == 135
    assert counter["status_tokens"] == 69100
    assert counter["status_marker"] == "○"


def test_agent_screen_state_reports_visible_counter_evidence_and_advancement():
    first = "✽ Tomfoolering… (7m 12s · ↓ 30.1k tokens · almost done thinking with xhigh effort)"
    second = first.replace("7m 12s", "7m 13s")

    first_state = prompt_detector.agent_screen_state(first, pane_target="%counter-advances", now=1000.0)
    second_state = prompt_detector.agent_screen_state(second, pane_target="%counter-advances", now=1001.0)

    assert first_state["key"] == "working"
    assert first_state["activity_source"] == "visible-counter"
    assert first_state["status_counter_advanced"] is False
    assert second_state["key"] == "working"
    assert second_state["status_counter_advanced"] is True
    assert second_state["status_elapsed_seconds"] == 433


def test_agent_screen_state_reports_token_counter_advancement():
    first = "✽ Tomfoolering… (7m 12s · ↓ 30.1k tokens · almost done thinking with xhigh effort)"
    second = first.replace("30.1k", "30.2k")

    prompt_detector.agent_screen_state(first, pane_target="%counter-token-advances", now=1000.0)
    state = prompt_detector.agent_screen_state(second, pane_target="%counter-token-advances", now=1001.0)

    assert state["key"] == "working"
    assert state["status_counter_advanced"] is True
    assert state["status_tokens"] == 30200


def test_agent_screen_state_prefers_codex_pursuing_goal_elapsed_for_display():
    visible_text = "\n".join([
        "◦ Working (1m 46s • esc to interrupt)",
        "› Implement feature",
        "  gpt-5.5 xhigh · ~ · Main [default]                         Pursuing goal (3h 45m)",
    ])

    state = prompt_detector.agent_screen_state(visible_text, pane_target="%codex-goal-display", now=1000.0)

    assert state["key"] == "working"
    assert state["status_elapsed_seconds"] == 106
    assert state["goal_elapsed_seconds"] == 13500
    assert state["display_elapsed_seconds"] == 13500


def test_codex_pursuing_goal_elapsed_does_not_advance_stale_counter():
    first = "\n".join([
        "◦ Working (1m 46s • esc to interrupt)",
        "› Implement feature",
        "  gpt-5.5 xhigh · ~ · Main [default]                         Pursuing goal (3h 45m)",
    ])
    second = first.replace("3h 45m", "3h 46m")

    prompt_detector.agent_screen_state(first, pane_target="%codex-goal-stale", now=1000.0)
    state = prompt_detector.agent_screen_state(second, pane_target="%codex-goal-stale", now=1080.0)

    assert state["key"] == "idle"
    assert state["status_counter_advanced"] is False
    assert state["negative_reason"] == "stale visible status counter"
    assert state["display_elapsed_seconds"] == 13560


def test_agent_screen_state_stales_repeated_unchanged_visible_counter():
    visible_text = "✽ Tomfoolering… (7m 12s · ↓ 30.1k tokens · almost done thinking with xhigh effort)"

    prompt_detector.agent_screen_state(visible_text, pane_target="%counter-stale", now=1000.0)
    state = prompt_detector.agent_screen_state(visible_text, pane_target="%counter-stale", now=1080.0)

    assert state["key"] == "idle"
    assert state["activity_source"] == "visible-counter"
    assert state["negative_reason"] == "stale visible status counter"


def test_visible_counter_is_stale_when_real_output_follows_it():
    visible_text = "\n".join([
        "✽ Tomfoolering… (7m 12s · ↓ 30.1k tokens · almost done thinking with xhigh effort)",
        "build finished",
        "keivenc@host$ ",
    ])

    assert prompt_detector.visible_agent_status_counter(visible_text) is None
    assert prompt_detector.agent_screen_state(visible_text)["key"] == "idle"


def test_claude_multi_agent_header_does_not_hide_live_approval_prompt():
    visible_text = "\n".join([
        "⠿ Running 2 agents…",
        "  ├ Verify detector fixtures · 14 tool uses · 31.2k tokens",
        "  └ Check current Claude pane state · 23 tool uses · 77.5k tokens",
        "",
        claude_bash_prompt_with_footer(" Esc to cancel · Tab to amend · ctrl+e to explain"),
    ])

    prompt_state = prompt_detector.approval_prompt_state(visible_text)
    screen_state = prompt_detector.agent_screen_state(visible_text)

    assert prompt_state["visible"] is True
    assert prompt_state["type"] == "bash"
    assert screen_state["key"] == "approval"
    assert screen_state["key"] != "working"


def test_approval_prompt_ignores_codex_bottom_chrome_after_footer():
    visible_text = "\n".join([
        "Would you like to run the following command?",
        "  echo hi",
        "",
        "❯ 1. Yes",
        "  2. No",
        "Enter to select",
        "╭────────────────────────────────────────────╮",
        "│ Use /skills to list available skills       │",
        "│ >                                          │",
        "╰────────────────────────────────────────────╯",
        "gpt-5.5 xhigh   ~/yolomux.dev2",
    ])
    assert prompt_detector.approval_prompt_has_later_activity(visible_text) is False


def test_detect_prompt_real_prompt_shapes_and_bottom_most_prompt_wins():
    codex_pane = "\n".join([
        "◦ Running gh api repos/ai-project/project/pulls/9579/comments",
        "",
        "  Would you like to run the following command?",
        "",
        "  Reason: Do you want to allow GitHub network access so I can fetch PR #9579 status?",
        "",
        "  $ gh api repos/ai-project/project/pulls/9579/comments",
        "",
        "› 1. Yes, proceed (y)",
        "  2. Yes, and don't ask again for commands that start with `gh api` (p)",
        "  3. No, and tell Codex what to do differently (esc)",
    ])
    assert prompt_detector.detect_prompt(codex_pane) == "bash"
    assert prompt_detector.prompt_text(codex_pane) == "Would you like to run the following command?"

    assert prompt_detector.detect_prompt(" Do you want to delete old_script.sh?\n ❯ 1. Yes\n   2. No\n") is None

    stale_then_current = "\n".join([
        " Do you want to proceed?",
        " ❯ 1. Yes",
        "   2. No",
        "",
        "● Some more work",
        "",
        " Do you want to make this edit to SKILL.md?",
        " ❯ 1. Yes",
        "   2. Yes, and allow Claude to edit its own settings",
        "   3. No",
    ])
    assert prompt_detector.detect_prompt(stale_then_current) == "file"


def test_detect_prompt_handles_real_codex_wrapped_command_header():
    codex_pane = "\n".join([
        "› Run sleep 10",
        "",
        "• Running sleep 10",
        "",
        "",
        "  Would you like to run the following comman",
        "",
        "  $ sleep 10",
        "",
        "› 1. Yes, proceed (y)",
        "  2. Yes, and don't ask again for commands",
        "     that start with `sleep 10` (p)",
        "  3. No, and tell Codex what to do",
        "     differently (esc)",
        "",
        "  Press enter to confirm or esc to cancel",
    ])

    prompt_state = prompt_detector.approval_prompt_state(codex_pane, codex_pane)
    screen_state = prompt_detector.agent_screen_state(codex_pane)

    assert prompt_state["visible"] is True
    assert prompt_state["type"] == "bash"
    assert prompt_state["agent"] == "codex"
    assert prompt_state["prompt_kind"] == "shell-command"
    assert prompt_state["question_text"] == "Would you like to run the following command?"
    assert prompt_state["command"] == "sleep 10"
    assert prompt_state["selected_option"] == 1
    assert screen_state["key"] == "approval"


def test_detect_prompt_handles_real_claude_wrapped_plan_prompt():
    claude_pane = "\n".join([
        "  ──────────────────────────────────────────────",
        "   Ready to code?",
        "",
        "   Here is Claude's plan:",
        "   Add a temporary line to README.md after approval.",
        "",
        "  ──────────────────────────────────────────────",
        "   Claude has written up a plan and is ready to",
        "   execute. Would you like to proceed?",
        "",
        "   ❯ 1. Yes, and use auto mode",
        "     2. Yes, manually approve edits",
        "     3. Tell Claude what to change",
    ])

    prompt_state = prompt_detector.approval_prompt_state(claude_pane, claude_pane)
    screen_state = prompt_detector.agent_screen_state(claude_pane)

    assert prompt_state["visible"] is True
    assert prompt_state["type"] == "plan"
    assert prompt_state["agent"] == "claude"
    assert prompt_state["prompt_kind"] == "plan-approval"
    assert prompt_state["question_text"] == "Claude has written up a plan and is ready to execute. Would you like to proceed?"
    assert prompt_state["selected_option"] == 1
    assert screen_state["key"] == "approval"


def test_action_for_prompt_preserves_codex_bash_option_policy():
    assert prompt_detector.action_for_prompt("bash") == "option1"
    assert prompt_detector.action_for_prompt("file") == "option2"
    assert prompt_detector.action_for_prompt("plan") == "option1"
    assert prompt_detector.action_for_prompt("tool") == "option2"
    assert prompt_detector.action_for_prompt(None) is None
    assert prompt_detector.action_for_prompt("unknown") is None
    assert prompt_detector.action_for_bash_prompt(
        "  Would you like to run the following command?\n"
        "  $ gh api repos/ai-project/project/pulls/9579/comments\n"
        "› 1. Yes, proceed (y)\n"
        "  2. Yes, and don't ask again for commands that start with `gh api` (p)\n"
    ) == "option2"
    assert prompt_detector.action_for_bash_prompt(
        "  Would you like to run the following command?\n"
        "  $ ~/ai-config/claude/bin/dyn_gh_ops.py pr-status --pr 9579\n"
        "› 1. Yes, proceed (y)\n"
        "  2. Yes, and don't ask again for commands that start with "
        "`'~/ai-config/claude/bin/dyn_gh_ops.py' pr-status --pr 9579` (p)\n"
    ) == "option1"


def test_approval_prompt_state_reports_selected_codex_option():
    visible_text = "\n".join([
        "  Would you like to run the following command?",
        "",
        "  $ curl -sk -u yolomux:yolomux https://localhost:19077/",
        "",
        "  1. Yes, proceed (y)",
        "› 2. Yes, and don't ask again for commands that start with `curl -sk -u` (p)",
        "  3. No, and tell Codex what to do differently (esc)",
    ])

    state = prompt_detector.approval_prompt_state(visible_text)

    assert state["visible"] is True
    assert state["type"] == "bash"
    assert state["yes_selected"] is False
    assert state["selected_option"] == 2
    assert state["action"] == "option1"


def test_prompt_hash_includes_command_context():
    yes_no_prompt = claude_bash_prompt_with_footer(
        " Esc to cancel · Tab to amend · ctrl+e to explain",
    )
    other_prompt = yes_no_prompt.replace("echo one", "echo two")

    assert prompt_detector.prompt_hash(yes_no_prompt) == prompt_detector.prompt_hash(yes_no_prompt)
    assert prompt_detector.prompt_hash(yes_no_prompt) != prompt_detector.prompt_hash(other_prompt)


def test_approval_prompt_state_extracts_command_and_dangerous_flag():
    pane_text = "\n".join([
        "─────────────────────────────────────────────",
        " Bash command",
        "",
        "   rm -rf /tmp/foo",
        "   Delete temp directory",
        "",
        " Permission rule Bash requires confirmation for this command.",
        "",
        " Do you want to proceed?",
        " ❯ 1. Yes",
        "   2. No",
    ])

    state = prompt_detector.approval_prompt_state(pane_text, pane_text)

    assert state["visible"] is True
    assert state["type"] == "bash"
    assert state["action"] == "option1"
    assert state["command"] == "rm -rf /tmp/foo"
    assert state["dangerous"] is True


def test_visible_choice_prompt_text_detects_current_user_question():
    visible_text = "\n".join([
        "Which backend should I use?",
        "❯ 1. vLLM",
        "  2. SGLang",
        "",
    ])

    assert prompt_detector.visible_choice_prompt_text(visible_text) == "Which backend should I use?\n1. vLLM\n2. SGLang"
    assert prompt_detector.agent_screen_state(visible_text)["key"] == "needs-input"


def test_visible_choice_prompt_text_ignores_stale_user_question_above_idle_prompt():
    visible_text = "\n".join([
        "❯ Where are the DOIT files?",
        "",
        "● They're gone from ~/yolomux.dev2 — and that's by the project's design, not a loss.",
        "",
        "✻ Baked for 1m 33s · 1 shell still running",
        "",
        "❯ ",
    ])

    assert prompt_detector.visible_choice_prompt_text(visible_text) == ""
    assert prompt_detector.agent_screen_state(visible_text)["key"] == "idle"


def test_visible_choice_prompt_text_ignores_claude_tip_question_chrome():
    visible_text = "\n".join([
        "❯ Plan this harmless YOLOmux E2E task: run `sleep 5` and then report done. Stop for approval before executing.",
        "",
        "✻ Composing…",
        "  ⎿  Tip: Did you know you can drag and drop image files into your terminal?",
        "",
        "────────────────────────────────────────────────────────────────",
        "❯ ",
        "────────────────────────────────────────────────────────────────",
        "  ⏸ plan mode on (shift+tab to cycle) · esc to interrupt",
    ])

    assert prompt_detector.visible_choice_prompt_text(visible_text) == ""
    assert prompt_detector.agent_screen_state(visible_text)["key"] == "idle"


def test_ask_user_question_ui_is_needs_input_not_auto_approved():
    # Claude Code's AskUserQuestion multi-option UI (image 20260602-014). The selected option
    # is box-highlighted (no ❯), and a preview box / "Notes:" / "Chat about this" sit between the options
    # and the footer. It must be flagged needs-input, but is NOT a yes/no permission prompt.
    visible_text = "\n".join([
        "How should the YO!info | YO!agent sub-tab toggle look inside the merged panel?",
        "  1. Segmented control under pane tabs",
        "  2. Pills in the content header",
        "┌──────────────────────────────┐",
        "│ Preview: segmented control…   │",
        "└──────────────────────────────┘",
        "Notes: press n to add notes",
        "Chat about this",
        "Enter to select · ↑/↓ to navigate · n to add notes · Tab to switch questions · Esc to cancel",
    ])

    assert prompt_detector.detect_prompt(visible_text) is None, "AskUserQuestion is not a yes/no permission prompt"
    state = prompt_detector.agent_screen_state(visible_text)
    assert state["key"] == "needs-input"
    assert state["text"] == "How should the YO!info | YO!agent sub-tab toggle look inside the merged panel?"


def test_ask_user_question_footer_parts_are_recognized():
    # The AskUserQuestion footer hints ("↑/↓ to navigate", "n to add notes", "Tab to switch questions")
    # count as a footer line so the block is bounded correctly.
    footer = "Enter to select · ↑/↓ to navigate · n to add notes · Tab to switch questions · Esc to cancel"
    assert prompt_detector._is_footer_hint_line(footer)
    assert prompt_detector._is_ask_user_question_footer(footer)


def test_prompt_trailing_ui_line_accepts_unicode_task_glyphs():
    # a Ctrl-T task list using □/✓ (U+25A1/U+2713, this Claude version) below a working
    # footer must read as prompt-trailing chrome, not new output — else visible_agent_working flips to
    # False and the YO ball stops spinning while the agent works.
    # the new glyph rows are recognized as prompt-trailing UI; the ballot-box family still is too.
    assert prompt_detector._is_prompt_trailing_ui_line("  □ Pending task") is True
    assert prompt_detector._is_prompt_trailing_ui_line("  ✓ Done task") is True
    assert prompt_detector._is_prompt_trailing_ui_line("✗ Failed task") is True
    assert prompt_detector._is_prompt_trailing_ui_line("  ☐ Old-style pending") is True
