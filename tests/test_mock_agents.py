import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

import pytest
import yaml

from yolomux_lib.agent_tui import classify_agent_pane
from yolomux_lib.tmux_utils import YOLOMUX_TMUX_SOCKET_ENV


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import mock_agent_common  # noqa: E402

PROMPT_CORPUS_DIR = REPO_ROOT / "tests" / "fixtures" / "prompt_corpus"


def load_structured_fixture(path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def fixture_path(inventory_path, file_name):
    if inventory_path.parent.name == "captures":
        return inventory_path.parent / file_name
    return PROMPT_CORPUS_DIR / file_name


def fixture_visible_text(path):
    data = load_structured_fixture(path)
    return str(data.get("raw_capture") or data.get("visible_text") or "")


def root_inventory_cases():
    inventory_path = PROMPT_CORPUS_DIR / "inventory.yaml"
    inventory = load_structured_fixture(inventory_path)
    cases = []
    for item in inventory["fixtures"]:
        path = fixture_path(inventory_path, item["file"])
        cases.append({"inventory": item, "data": load_structured_fixture(path), "path": path, "text": fixture_visible_text(path)})
    return cases


def promoted_capture_cases():
    inventory_path = PROMPT_CORPUS_DIR / "captures" / "inventory.yaml"
    inventory = load_structured_fixture(inventory_path)
    cases = []
    for item in inventory["fixtures"]:
        path = fixture_path(inventory_path, item["file"])
        data = load_structured_fixture(path)
        if "expected_promoted" not in data:
            continue
        cases.append({"inventory": item, "data": data, "path": path, "text": fixture_visible_text(path)})
    return cases


def case_agent(case):
    data = case["data"]
    expected = case["inventory"].get("expected") if isinstance(case["inventory"].get("expected"), dict) else {}
    agent = str(data.get("agent") or expected.get("agent") or "")
    if agent in {"claude", "codex"}:
        return agent
    name = str(case["path"])
    if "claude" in name:
        return "claude"
    return "codex"


def case_command_name(case):
    data = case["data"]
    inventory = case["inventory"]
    case_name = str(data.get("case_name") or inventory.get("case_name") or inventory.get("scenario") or case["path"].stem)
    agent = str(data.get("agent") or inventory.get("expected", {}).get("agent") or "")
    if agent in {"claude", "codex"}:
        return f"{agent}_{case_name}"
    return case_name


def tmux_cmd(tmux_binary, socket_path, *args, timeout=8):
    return subprocess.run([tmux_binary, "-S", str(socket_path), *args], capture_output=True, text=True, timeout=timeout, check=False)


def capture(tmux_binary, socket_path, session):
    return tmux_cmd(tmux_binary, socket_path, "capture-pane", "-p", "-t", f"{session}:").stdout or ""


def visible_needles(text):
    lines = [line.strip()[:80] for line in str(text or "").splitlines() if line.strip()]
    needles = []
    for line in lines:
        if line in {"❯", "›", ">"}:
            continue
        if re.fullmatch(r"[─━╌╍▔╭╮╰╯│ ]+", line):
            continue
        if line.startswith(("│", "╭", "╰", "▐", "▝", "▘", "gpt-5.5 ", "Opus ", "Tip: ", "⏵⏵ ", "▶▶ ", "⏸ ")):
            continue
        if line.startswith(("⚠ Safe mode:", "Restart without --safe-mode")):
            continue
        if line in {'› Implement {feature}', '› Write tests for @filename', '› Explain this codebase', '❯ Try "fix typecheck errors"'}:
            continue
        if len(line) < 8:
            continue
        needles.append(line)
    return list(dict.fromkeys(needles))


def wait_for_mockcase_render(tmux_binary, socket_path, session, expected_text, timeout=10):
    needles = visible_needles(expected_text)
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        last = capture(tmux_binary, socket_path, session)
        if needles:
            if any(needle in last for needle in needles):
                return True, last
        elif all(prompt not in last for prompt in ("Implement {feature}", "Write tests for @filename", "Explain this codebase", 'Try "fix typecheck errors"')):
            return True, last
        time.sleep(0.2)
    return False, last


def classify_mockcase_until(target, expected_screen_key, *, timeout=3, expected_composer_key=None, **kwargs):
    deadline = time.time() + timeout
    last_state = None
    while time.time() < deadline:
        last_state = classify_agent_pane(target, **kwargs)
        screen_matches = last_state.screen["key"] == expected_screen_key
        composer_matches = expected_composer_key is None or last_state.composer.key == expected_composer_key
        if screen_matches and composer_matches:
            return last_state
        time.sleep(0.1)
    return last_state or classify_agent_pane(target, **kwargs)


def run_mockcase(monkeypatch, tmp_path, case):
    tmux_binary = shutil.which("tmux")
    if not tmux_binary:
        pytest.skip("tmux is not installed")
    agent = case_agent(case)
    sock_base = Path("/tmp") / f"yomockcase-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    sock_base.mkdir(mode=0o700)
    socket_path = sock_base / "s"
    session = f"ymock-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv(YOLOMUX_TMUX_SOCKET_ENV, str(socket_path))
    created = tmux_cmd(
        tmux_binary,
        socket_path,
        "new-session",
        "-d",
        "-s",
        session,
        "-x",
        "120",
        "-y",
        "40",
        f"cd {REPO_ROOT} && exec python3 tools/{agent}.py --mock",
    )
    assert created.returncode == 0, created.stderr or created.stdout
    try:
        prompt_text = 'Try "fix typecheck errors"' if agent == "claude" else "Explain this codebase"
        rendered, pane = wait_for_mockcase_render(tmux_binary, socket_path, session, prompt_text)
        assert rendered, pane
        tmux_cmd(tmux_binary, socket_path, "send-keys", "-t", f"{session}:", f"mockcase {case_command_name(case)}", "Enter")
        rendered, pane = wait_for_mockcase_render(tmux_binary, socket_path, session, case["text"])
        assert rendered, f"{case['path'].name} did not render through mockcase:\n{pane}"
        return session
    except Exception:
        tmux_cmd(tmux_binary, socket_path, "kill-server")
        shutil.rmtree(sock_base, ignore_errors=True)
        raise
    finally:
        tmp_path.joinpath("mockcase_socket").write_text(str(socket_path), encoding="utf-8")
        tmp_path.joinpath("mockcase_session").write_text(session, encoding="utf-8")


def cleanup_mockcase(tmp_path):
    socket_file = tmp_path / "mockcase_socket"
    session_file = tmp_path / "mockcase_session"
    if socket_file.exists():
        socket_path = Path(socket_file.read_text(encoding="utf-8"))
        tmux_binary = shutil.which("tmux")
        if tmux_binary:
            tmux_cmd(tmux_binary, socket_path, "kill-server")
        shutil.rmtree(socket_path.parent, ignore_errors=True)
    if session_file.exists():
        session_file.unlink()
    if socket_file.exists():
        socket_file.unlink()


def test_mock_fixture_list_reports_cursor_status(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "choice_question",
            "path": PROMPT_CORPUS_DIR / "captures" / "choice_question.yaml",
            "cursor": {"x": 2, "y": 37},
            "expected": {"attention_label": "ASK?", "screen_key": "needs-input"},
        },
        {
            "agent": "claude",
            "case_name": "synthetic_plan",
            "path": PROMPT_CORPUS_DIR / "synthetic" / "synthetic_plan.yaml",
            "cursor": {},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")

    mock_agent_common.print_mock_fixture_list(include_idle=True)

    output = capsys.readouterr().out
    assert "codex: [ASK?] choice_question" in output
    assert "cursor=2,37" in output
    assert "claude: synthetic_plan" not in output


def test_mock_fixture_list_clips_long_rows_to_terminal_width(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "claude",
            "case_name": "interrupted_what_should_claude_do_instead",
            "path": PROMPT_CORPUS_DIR / "captures" / "interrupted_what_should_claude_do_instead__claude-code-2.1.185_20260621.yaml",
            "cursor": {},
            "expected": {"attention_label": "ASK?", "screen_key": "needs-input"},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "claude")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 72)

    mock_agent_common.print_mock_fixture_list(include_idle=True)

    output = capsys.readouterr().out
    lines = output.splitlines()
    assert any(line.startswith("  ⎿  claude: [ASK?] interrupted_what_should_claude_do") for line in lines)
    assert all(len(mock_agent_common.ANSI_RE.sub("", line)) <= 72 for line in lines)
    assert any(line.endswith("…") for line in lines)


def test_mocklist_alias_prints_fixture_list(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "choice_question",
            "path": PROMPT_CORPUS_DIR / "captures" / "choice_question.yaml",
            "cursor": {"x": 2, "y": 37},
            "expected": {"attention_label": "ASK?", "screen_key": "needs-input"},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")

    mock_agent_common.handle_command("mocklist", {})

    output = capsys.readouterr().out
    assert "Mock fixture cases" in output
    assert "choice_question" in output


def test_mock_list_defaults_to_current_agent_and_list_all_includes_shared_cases(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "codex_idle_case",
            "path": PROMPT_CORPUS_DIR / "captures" / "codex_case.yaml",
            "cursor": {"x": 2, "y": 37},
            "expected": {"screen_key": "idle", "reason_code": "idle"},
        },
        {
            "agent": "codex",
            "case_name": "codex_question_case",
            "path": PROMPT_CORPUS_DIR / "captures" / "codex_question_case.yaml",
            "cursor": {"x": 2, "y": 37},
            "expected": {"screen_key": "needs-input"},
        },
        {
            "agent": "claude",
            "case_name": "claude_idle_case",
            "path": PROMPT_CORPUS_DIR / "captures" / "claude_case.yaml",
            "cursor": {"x": 3, "y": 34},
            "expected": {"screen_key": "idle", "reason_code": "idle"},
        },
        {
            "agent": "claude",
            "case_name": "claude_approval_case",
            "path": PROMPT_CORPUS_DIR / "captures" / "claude_approval_case.yaml",
            "cursor": {"x": 3, "y": 34},
            "expected": {"screen_key": "approval", "approval_visible": True},
        },
        {
            "agent": "unknown",
            "case_name": "unknown_case",
            "path": PROMPT_CORPUS_DIR / "synthetic" / "unknown_case.yaml",
            "cursor": {},
            "expected": {"screen_key": "needs-input"},
        },
        {
            "agent": "generic",
            "case_name": "generic_case",
            "path": PROMPT_CORPUS_DIR / "synthetic" / "generic_case.yaml",
            "cursor": {},
            "expected": {"screen_key": "working"},
        },
    ])

    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")
    mock_agent_common.handle_command("mock list", {})
    codex_output = capsys.readouterr().out
    assert "codex: [ASK?] codex_question_case" in codex_output
    assert "codex_idle_case" not in codex_output
    assert "unknown: unknown_case" not in codex_output
    assert "generic: generic_case" not in codex_output
    assert "claude_approval_case" not in codex_output

    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "claude")
    mock_agent_common.handle_command("mock list", {})
    claude_output = capsys.readouterr().out
    assert "claude: [YOLO?] claude_approval_case" in claude_output
    assert "claude_idle_case" not in claude_output
    assert "unknown: unknown_case" not in claude_output
    assert "generic: generic_case" not in claude_output
    assert "codex_question_case" not in claude_output

    mock_agent_common.handle_command("mock list all", {})
    all_output = capsys.readouterr().out
    assert "claude: [idle] claude_idle_case" in all_output
    assert "claude: [YOLO?] claude_approval_case" in all_output
    assert "unknown: [ASK?] unknown_case" in all_output
    assert "generic: [RUN] generic_case" in all_output
    assert "codex_question_case" not in all_output

    mock_agent_common.handle_command("mock list idle", {})
    idle_output = capsys.readouterr().out
    assert "claude: [idle] claude_idle_case" in idle_output
    assert "claude_approval_case" not in idle_output


def test_claude_default_mock_list_has_no_unknown_or_generic_cases(monkeypatch):
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", None)
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "claude")

    rows = mock_agent_common.mock_fixture_list_cases()

    assert rows
    assert not {str(case.get("agent") or "") for case in rows} & {"unknown", "generic"}
    assert all(mock_agent_common.mock_fixture_outcome_label(case) != "idle" for case in rows)
    assert any(mock_agent_common.mock_fixture_outcome_label(case) == "ASK?" for case in rows)
    assert any(mock_agent_common.mock_fixture_outcome_label(case) == "YOLO?" for case in rows)
    assert any(mock_agent_common.mock_fixture_outcome_label(case) == "RUN" for case in rows)
    idle_rows = mock_agent_common.mock_fixture_list_cases(include_idle=True, only_idle=True)
    assert idle_rows
    for case in idle_rows:
        assert str(case.get("agent") or "") == "claude"
        expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
        label = mock_agent_common.mock_fixture_outcome_label(case)
        assert expected.get("screen_key") == "idle"
        assert label == "idle"


def test_mock_fixture_list_dedupes_same_fixture_identity(monkeypatch, capsys):
    duplicate_path = PROMPT_CORPUS_DIR / "captures" / "choice_question_real__codex-cli-0.141.0_20260620.yaml"
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 160)
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "choice_question_real",
            "path": duplicate_path,
            "cursor": {"x": 36, "y": 10},
            "expected": {"attention_label": "ASK?"},
        },
        {
            "agent": "codex",
            "case_name": "choice_question_real",
            "path": duplicate_path,
            "cursor": {"x": 36, "y": 10},
            "expected": {"attention_label": "ASK?"},
        },
        {
            "agent": "codex",
            "case_name": "choice_question_real",
            "path": PROMPT_CORPUS_DIR / "captures" / "choice_question_real_other.yaml",
            "cursor": {"x": 36, "y": 10},
            "expected": {"attention_label": "ASK?"},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")

    mock_agent_common.print_mock_fixture_list()

    output = capsys.readouterr().out
    assert output.count("choice_question_real__codex-cli-0.141.0_20260620.yaml") == 1
    assert "choice_question_real_other.yaml" in output


def test_mock_fixture_list_prefers_real_capture_over_synthetic_duplicate(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 160)
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "idle_empty_prompt",
            "path": PROMPT_CORPUS_DIR / "captures" / "idle_empty_prompt__codex-cli-0.141.0_20260620.yaml",
            "cursor": {"x": 0, "y": 0},
            "expected": {"screen_key": "idle", "reason_code": "idle"},
        },
        {
            "agent": "codex",
            "case_name": "idle_empty_prompt",
            "path": PROMPT_CORPUS_DIR / "synthetic" / "idle_empty_prompt__codex-cli-synthetic_20260620.yaml",
            "cursor": {"x": 2, "y": 0},
            "expected": {"screen_key": "idle", "reason_code": "idle"},
        },
        {
            "agent": "codex",
            "case_name": "synthetic_only_case",
            "path": PROMPT_CORPUS_DIR / "synthetic" / "synthetic_only_case.yaml",
            "cursor": {},
            "expected": {"approval_visible": True},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")

    mock_agent_common.print_mock_fixture_list(include_idle=True)

    output = capsys.readouterr().out
    assert "idle_empty_prompt__codex-cli-0.141.0_20260620.yaml" in output
    assert "idle_empty_prompt__codex-cli-synthetic_20260620.yaml" not in output
    assert "codex: [YOLO?] synthetic_only_case" in output
    assert "synthetic_only_case.yaml" in output


def test_mock_fixture_outcome_labels_come_from_expected_metadata():
    assert mock_agent_common.mock_fixture_outcome_label({"expected": {"attention_label": "ASK?"}}) == "ASK?"
    assert mock_agent_common.mock_fixture_outcome_label({"expected": {"approval_visible": True}}) == "YOLO?"
    assert mock_agent_common.mock_fixture_outcome_label({"expected": {"screen_key": "needs-input"}}) == "ASK?"
    assert mock_agent_common.mock_fixture_outcome_label({"expected": {"screen_key": "working"}}) == "RUN"
    assert mock_agent_common.mock_fixture_outcome_label({"expected": {"screen_key": "input-draft"}}) == "draft"
    assert mock_agent_common.mock_fixture_outcome_label({"expected": {"screen_key": "idle"}}) == "idle"


def test_mock_fixture_cursor_infers_prompt_above_status_line():
    lines = [
        "› Implement {feature}",
        "",
        "  gpt-5.5 xhigh · ~/yolomux.dev8002",
    ]

    assert mock_agent_common.mock_fixture_render_cursor(lines, {}, 10, 7) == (2, 7)


def test_mock_fixture_cursor_uses_selected_option_for_choice_prompt():
    lines = [
        "Question?",
        "› 1. Yes",
        "  2. No",
        "",
        "  gpt-5.5 xhigh · ~/yolomux.dev8002",
    ]
    group = {"idxs": [1, 2], "selected": 0, "glyph": "›"}

    assert mock_agent_common.mock_fixture_render_cursor(lines, {}, 10, 5, group) == (0, 6)


def test_codex_mock_question_strips_captured_footer_and_reserves_live_footer(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "choice_question",
            "keys": {"choice_question"},
            "path": PROMPT_CORPUS_DIR / "captures" / "choice_question.yaml",
            "styled_capture": (
                "› Ask me this question and wait for my answer instead of choosing yourself: Which YOLOmux verifier mode should we use?\n"
                "  1. Pane capture 2. Transcript capture\n\n\n"
                "• Which YOLOmux verifier mode should we use?\n\n"
                "  1. Pane capture\n"
                "  2. Transcript capture\n\n\n"
                "› Improve documentation in @filename\n\n"
                "  gpt-5.5 xhigh · ~/yolomux.dev8001"
            ),
            "cursor": {},
            "expected": {"ask": True, "screen_key": "needs-input"},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 120)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 20)
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "›")
    monkeypatch.setattr(mock_agent_common, "MODEL", "gpt-5.4-mini")
    monkeypatch.setattr(mock_agent_common, "EFFORT", "medium")
    monkeypatch.setattr(mock_agent_common, "display_cwd", lambda: "~/yolomux.dev8002")
    state = {}

    mock_agent_common.cmd_mock_fixture(state, "choice_question", freeze_static=False)

    output = capsys.readouterr().out
    assert "Which YOLOmux verifier mode should we use?" in output
    assert "gpt-5.5 xhigh" not in output
    assert "› Improve documentation in @filename" not in output
    assert "\x1b[18;1H\x1b[2K› \x1b[2mExplain this codebase\x1b[0m" in output
    assert "\x1b[20;1H\x1b[2K  gpt-5.4-mini medium · ~/yolomux.dev8002" in output
    assert state.get("pending", "") == ""


def test_codex_mock_approval_reserves_footer_rows(monkeypatch, capsys):
    class TtyInput:
        def isatty(self):
            return True

    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "reappeared_prompt_after_tool",
            "keys": {"reappeared_prompt_after_tool"},
            "path": PROMPT_CORPUS_DIR / "synthetic" / "reappeared_prompt_after_tool.yaml",
            "styled_capture": (
                "• Bash(echo before)\n"
                "  └ before\n\n"
                "────────────────────────────────────────────────────────────────\n\n"
                "Codex wants to run a shell command\n\n"
                "Would you like to run the following command?\n\n"
                "  Reason: The previous tool call finished and Codex redrew the approval.\n\n"
                "  $ echo after-redraw\n\n"
                "› 1. Yes, proceed (y)\n"
                "  2. No, and tell Codex what to do differently (esc)\n\n"
                "Press enter to confirm or esc to cancel\n\n"
                "› Improve documentation in @filename\n\n"
                "  gpt-5.5 xhigh · ~/yolomux.dev8001"
            ),
            "cursor": {},
            "expected": {"approval_visible": True, "screen_key": "approval"},
        },
    ])
    monkeypatch.setattr(sys, "stdin", TtyInput())
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 120)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 20)
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "›")
    monkeypatch.setattr(mock_agent_common, "SELECTOR_GLYPH", "›")
    monkeypatch.setattr(mock_agent_common, "MODEL", "gpt-5.4-mini")
    monkeypatch.setattr(mock_agent_common, "EFFORT", "medium")
    monkeypatch.setattr(mock_agent_common, "display_cwd", lambda: "~/yolomux.dev8002")
    state = {}

    mock_agent_common.cmd_mock_fixture(state, "reappeared_prompt_after_tool", freeze_static=False)

    output = capsys.readouterr().out
    assert "Would you like to run the following command?" in output
    assert "Press enter to confirm or esc to cancel" in output
    assert "gpt-5.5 xhigh" not in output
    assert "› Improve documentation in @filename" not in output
    assert "\x1b[18;1H\x1b[2K› \x1b[2mExplain this codebase\x1b[0m" in output
    assert "\x1b[20;1H\x1b[2K  gpt-5.4-mini medium · ~/yolomux.dev8002" in output
    assert state.get("pending") == "fixture"
    assert state.get("fixture_interactive") == "1"


def test_plain_mock_non_choice_fixture_prints_inline_without_clearing(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "completed_prompt",
            "keys": {"completed_prompt"},
            "path": PROMPT_CORPUS_DIR / "synthetic" / "completed_prompt.yaml",
            "styled_capture": (
                "Codex wants to run a shell command\n\n"
                "Would you like to run the following command?\n\n"
                "  $ echo done\n\n"
                "› 1. Yes, proceed (y)\n"
                "  2. No, and tell Codex what to do differently (esc)\n\n"
                "Press enter to confirm or esc to cancel\n"
                "● Ran echo done\n"
                "  ⎿ done\n\n"
                "› "
            ),
            "cursor": {"x": 1, "y": 5},
            "expected": {"ask": False, "approval_visible": False, "screen_key": "idle"},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 120)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 40)
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    state = {}

    mock_agent_common.cmd_mock_fixture(state, "completed_prompt", freeze_static=False)

    output = capsys.readouterr().out
    assert "\x1b[H\x1b[J" not in output
    assert output.startswith("\nCodex wants to run a shell command")
    assert output.endswith("› \n")
    assert "\x1b[6;2H" not in output
    assert state.get("pending") != "fixture"


def test_plain_mock_working_fixture_freezes_without_live_composer(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "goal_active",
            "keys": {"goal_active"},
            "path": PROMPT_CORPUS_DIR / "captures" / "goal_active.yaml",
            "styled_capture": (
                "• Working (55m 44s • esc to interrupt)\n\n\n"
                "› Implement {feature}\n\n"
                "  gpt-5.5 xhigh · ~ · Main [default]                 Pursuing goal (1h 30m)"
            ),
            "cursor": {},
            "expected": {"screen_key": "working"},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 120)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    state = {}

    mock_agent_common.cmd_mock_fixture(state, "goal_active", freeze_static=False)

    output = capsys.readouterr().out
    assert "\x1b[H\x1b[J" in output
    assert "› Implement {feature}" in output
    assert "Pursuing goal (1h 30m)" in output
    assert state.get("pending") == "fixture"
    assert state.get("fixture_interactive", "") == ""


def test_plain_codex_goal_active_fixture_enters_live_working_state(monkeypatch):
    class TtyBuffer(io.StringIO):
        def isatty(self):
            return True

    class TtyInput:
        def isatty(self):
            return True

    output = TtyBuffer()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stdin", TtyInput())
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "goal_active",
            "keys": {"goal_active"},
            "path": PROMPT_CORPUS_DIR / "captures" / "goal_active.yaml",
            "styled_capture": "historical transcript should not render",
            "cursor": {},
            "expected": {"screen_key": "working"},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 120)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    state = {}

    mock_agent_common.cmd_mock_fixture(state, "goal_active", freeze_static=False)

    rendered = output.getvalue()
    assert state.get("pending") == "codex-goal-active"
    assert "historical transcript should not render" not in rendered
    assert "\x1b[H\x1b[J" not in rendered
    assert "Working" in rendered
    assert "Pursuing goal" not in rendered


def test_plain_codex_run_fixtures_enter_live_working_state(monkeypatch):
    class TtyBuffer(io.StringIO):
        def isatty(self):
            return True

    class TtyInput:
        def isatty(self):
            return True

    output = TtyBuffer()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stdin", TtyInput())
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "working_command_counter",
            "keys": {"working_command_counter"},
            "path": PROMPT_CORPUS_DIR / "captures" / "working_command_counter.yaml",
            "styled_capture": "◦ Working (0s • esc to interrupt)\n\n› Improve documentation in @filename",
            "cursor": {},
            "expected": {"screen_key": "working", "reason_code": "busy"},
        },
        {
            "agent": "codex",
            "case_name": "working_spinner",
            "keys": {"working_spinner"},
            "path": PROMPT_CORPUS_DIR / "synthetic" / "working_spinner.yaml",
            "styled_capture": "• Working (1m 21s • esc to interrupt)\n\n› Implement the detector corpus",
            "cursor": {},
            "expected": {"screen_key": "working"},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 120)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    state: dict[str, str] = {}

    mock_agent_common.cmd_mock_fixture(state, "working_command_counter", freeze_static=False)
    first_render = output.getvalue()
    assert state.get("pending") == "codex-working"
    assert state.get("codex_working_base_seconds") == "0"
    assert "Improve documentation in @filename" not in first_render
    assert "• Working (0s • esc to interrupt)" in mock_agent_common.ANSI_RE.sub("", first_render)

    state.clear()
    output.truncate(0)
    output.seek(0)
    mock_agent_common.cmd_mock_fixture(state, "working_spinner", freeze_static=False)
    second_render = output.getvalue()
    assert state.get("pending") == "codex-working"
    assert state.get("codex_working_base_seconds") == "81"
    assert "Implement the detector corpus" not in second_render
    assert "• Working (1m 21s • esc to interrupt)" in mock_agent_common.ANSI_RE.sub("", second_render)


def test_plain_claude_working_fixture_replaces_captured_footer(monkeypatch):
    class TtyBuffer(io.StringIO):
        def isatty(self):
            return True

    class TtyInput:
        def isatty(self):
            return True

    output = TtyBuffer()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stdin", TtyInput())
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "claude",
            "case_name": "working_visible_counter",
            "keys": {"working_visible_counter"},
            "path": PROMPT_CORPUS_DIR / "captures" / "working_visible_counter.yaml",
            "styled_capture": (
                "❯ Plan this harmless verification task\n\n"
                "● Updated plan\n"
                "  ⎿  /plan to preview\n\n\n"
                "· Clauding… (11s · ↓ 471 tokens)\n"
                "────────────────────────────────────────────────────────────────\n"
                "❯ \n"
                "────────────────────────────────────────────────────────────────\n"
                "  ⏸ plan mode on (shift+tab to cycle) · esc to interrupt\n"
            ),
            "cursor": {},
            "expected": {"screen_key": "working", "reason_code": "busy"},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 120)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 20)
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "claude")
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "claude")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "❯")
    state: dict[str, str] = {}

    mock_agent_common.cmd_mock_fixture(state, "working_visible_counter", freeze_static=False)

    rendered = mock_agent_common.ANSI_RE.sub("", output.getvalue())
    assert state.get("pending") == "claude-working"
    assert state.get("claude_working_base_seconds") == "11"
    assert state.get("claude_working_base_tokens") == "471"
    assert "● Updated plan" in rendered
    assert rendered.count("Clauding") == 1
    assert rendered.count("plan mode on (shift+tab to cycle) · esc to interrupt") == 1
    assert rendered.count('Try "fix typecheck errors"') == 1


def test_claude_working_line_increments_from_capture(monkeypatch):
    state = {
        "claude_working_base_seconds": "11",
        "claude_working_base_tokens": "471",
        "claude_working_marker": "·",
        "claude_working_verb": "Clauding",
    }

    assert mock_agent_common.claude_working_line(11, state) == "· Clauding… (11s · ↓ 471 tokens)"
    assert mock_agent_common.claude_working_line(13, state) == "· Clauding… (13s · ↓ 519 tokens)"


def test_mock_fixture_dump_prints_agent_fixtures_with_separators(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "codex_case",
            "keys": {"codex_case"},
            "path": PROMPT_CORPUS_DIR / "captures" / "codex_case.yaml",
            "styled_capture": "codex visible text",
            "raw_capture": "",
            "cursor": {},
            "expected": {"screen_key": "working"},
        },
        {
            "agent": "claude",
            "case_name": "claude_case",
            "keys": {"claude_case"},
            "path": PROMPT_CORPUS_DIR / "captures" / "claude_case.yaml",
            "styled_capture": "claude visible text",
            "raw_capture": "",
            "cursor": {},
            "expected": {"screen_key": "working"},
        },
        {
            "agent": "generic",
            "case_name": "shared_idle",
            "keys": {"shared_idle"},
            "path": PROMPT_CORPUS_DIR / "synthetic" / "shared_idle.yaml",
            "styled_capture": "shared idle text",
            "raw_capture": "",
            "cursor": {},
            "expected": {"screen_key": "idle"},
        },
    ])

    mock_agent_common.print_mock_fixture_dump()

    output = capsys.readouterr().out
    assert "===== BEGIN FIXTURE 1/2: codex_case.yaml =====" in output
    assert "path: " + str(PROMPT_CORPUS_DIR / "captures" / "codex_case.yaml") in output
    assert "----- capture -----\ncodex visible text\n" in output
    assert "===== END FIXTURE: codex_case.yaml =====" in output
    assert "shared_idle.yaml" in output
    assert "shared idle text" in output
    assert "claude_case.yaml" not in output
    assert "claude visible text" not in output


def test_plain_codex_typed_draft_prefills_live_composer(monkeypatch):
    class TtyBuffer(io.StringIO):
        def isatty(self):
            return True

    class TtyInput:
        def isatty(self):
            return True

    output = TtyBuffer()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stdin", TtyInput())
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "typed_draft",
            "keys": {"typed_draft"},
            "path": PROMPT_CORPUS_DIR / "captures" / "typed_draft.yaml",
            "styled_capture": (
                "╭─────────────────────────────────────────────╮\n"
                "│ >_ OpenAI Codex (v0.141.0)                  │\n"
                "│ model:     gpt-5.5 xhigh   /model to change │\n"
                "│ directory: ~/yolomux.dev8001                │\n"
                "╰─────────────────────────────────────────────╯\n\n"
                "  Tip: Switch models or reasoning effort quickly with /model.\n\n\n"
                "› run the focused detector tests \n\n"
                "  gpt-5.5 xhigh · ~/yolomux.dev8001\n"
            ),
            "cursor": {},
            "expected": {"screen_key": "input-draft", "composer_key": "draft"},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 120)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "›")
    monkeypatch.setattr(mock_agent_common, "MODEL", "gpt-5.4-mini")
    monkeypatch.setattr(mock_agent_common, "EFFORT", "medium")
    monkeypatch.setattr(mock_agent_common, "display_cwd", lambda: "~/yolomux.dev8002")
    state: dict[str, str] = {}

    mock_agent_common.cmd_mock_fixture(state, "typed_draft", freeze_static=False)

    rendered = output.getvalue()
    assert state.get("composer_prefill") == "run the focused detector tests"
    assert state.get("pending", "") == ""
    assert "OpenAI Codex (v0.141.0)" not in rendered
    assert "gpt-5.5 xhigh" not in rendered
    assert "~/yolomux.dev8001" not in rendered
    assert "\x1b[10;1H\x1b[2K› run the focused detector tests" in rendered
    assert "\x1b[12;1H\x1b[2K  gpt-5.4-mini medium · ~/yolomux.dev8002" in rendered


def test_codex_goal_active_working_keys_interrupt_and_preserve_draft():
    text, cursor, action = mock_agent_common.apply_codex_working_key("queued", 6, "\x1b")
    assert (text, cursor, action) == ("queued", 6, "interrupt")

    text, cursor, action = mock_agent_common.apply_codex_working_key("queued", 6, "\x03")
    assert (text, cursor, action) == ("queued", 6, "interrupt")

    text, cursor, action = mock_agent_common.apply_codex_working_key("queued", 6, "\t")
    assert (text, cursor, action) == ("queued", 6, "queue")

    text, cursor, action = mock_agent_common.apply_codex_working_key("queued", 6, "!")
    assert (text, cursor, action) == ("queued!", 7, "")


def test_claude_plain_mock_scrollback_fixture_renders_above_footer(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "generic",
            "case_name": "scrollback_old_question",
            "keys": {"scrollback_old_question"},
            "path": PROMPT_CORPUS_DIR / "synthetic" / "scrollback_old_question.yaml",
            "styled_capture": (
                "Which backend should I use for this verification?\n"
                "❯ 1. vLLM\n"
                "  2. SGLang\n"
                "  3. TensorRT-LLM\n\n"
                "Enter to select · ↑/↓ to navigate · Esc to cancel\n"
                "● User answered: vLLM\n"
                "● Running tests now.\n"
                "  ⎿ ok\n\n"
                "❯"
            ),
            "cursor": {},
            "expected": {"ask": False, "approval_visible": False, "screen_key": "idle"},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 30)
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "claude")
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "claude")
    state = {"live_composer_footer_top": "26"}

    mock_agent_common.cmd_mock_fixture(state, "scrollback_old_question", freeze_static=False)
    mock_agent_common.render_live_composer("", 0, state=state)

    output = capsys.readouterr().out
    assert "Which backend should I use for this verification?" in output
    assert "● Running tests now." in output
    assert "\x1b[16;1HWhich backend should I use for this verification?" in output
    assert "\x1b[26;1H\x1b[2K" in output
    after_fixture_write = output.split("\x1b[16;1HWhich backend should I use for this verification?", 1)[1]
    assert "\x1b[16;1H\x1b[2K" not in after_fixture_write
    assert state.get("pending") != "fixture"


def test_fixture_choice_group_handles_claude_multiline_options():
    lines = [
        "Which YOLOmux verifier mode should we use?",
        "",
        "❯ 1. Pane capture",
        "     Capture the verification output directly from the terminal pane.",
        "  2. Transcript capture",
        "     Capture the verification output from the session transcript.",
        "  3. Type something.",
        "────────────────────────────────────────────────────────────────",
        "  4. Chat about this",
        "",
        "Enter to select · ↑/↓ to navigate · Esc to cancel",
    ]

    group = mock_agent_common.fixture_choice_group(lines)

    assert group is not None
    assert group["idxs"] == [2, 4, 6, 8]
    assert group["bodies"] == [
        "1. Pane capture",
        "2. Transcript capture",
        "3. Type something.",
        "4. Chat about this",
    ]
    assert group["indents"] == ["", "", "", ""]
    assert group["selected"] == 0
    assert group["glyph"] == "❯"


def test_claude_interactive_fixture_reserves_composer_footer(monkeypatch):
    class TtyBuffer(io.StringIO):
        def isatty(self):
            return True

    class TtyInput:
        def isatty(self):
            return True

    output = TtyBuffer()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stdin", TtyInput())
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "claude",
            "case_name": "multiple_choice_question",
            "keys": {"multiple_choice_question"},
            "path": PROMPT_CORPUS_DIR / "synthetic" / "multiple_choice_question.yaml",
            "styled_capture": (
                "Which backend should I use for this verification?\n"
                "❯ 1. vLLM\n"
                "  2. SGLang\n"
                "  3. TensorRT-LLM\n\n"
                "Enter to select · ↑/↓ to navigate · Esc to cancel"
            ),
            "cursor": {},
            "expected": {"ask": True, "screen_key": "needs-input"},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "claude")
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "claude")
    state = {}

    mock_agent_common.cmd_mock_fixture(state, "multiple_choice_question", freeze_static=False)

    rendered = output.getvalue()
    assert "\x1b[3;1HWhich backend should I use for this verification?" in rendered
    assert state["fixture_option_rows"] == "4,5,6"
    assert "tmux focus-events off" not in rendered
    assert "\x1b[9;1H\x1b[2K" in rendered
    assert '\x1b[10;1H\x1b[2K❯ \x1b[2mTry "fix typecheck errors"\x1b[0m' in rendered
    assert "\x1b[12;1H\x1b[2K  ? for shortcuts · ← for agents" in rendered
    assert state.get("pending") == "fixture"


def test_mock_fixture_without_selector_returns_to_composer(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "plain_question",
            "keys": {"plain_question"},
            "path": PROMPT_CORPUS_DIR / "captures" / "plain_question.yaml",
            "styled_capture": "• Which YOLOmux verifier mode should we use?\n\n  1. Pane capture\n  2. Transcript capture\n",
            "cursor": {},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")
    state = {}

    mock_agent_common.cmd_mock_fixture(state, "plain_question")

    assert state.get("pending", "") == ""
    assert state.get("fixture_interactive", "") == ""
    output = capsys.readouterr().out
    assert "Which YOLOmux verifier mode" in output


def test_mockcase_fixture_without_selector_stays_frozen(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "plain_question",
            "keys": {"plain_question"},
            "path": PROMPT_CORPUS_DIR / "captures" / "plain_question.yaml",
            "styled_capture": "• Which YOLOmux verifier mode should we use?\n\n  1. Pane capture\n  2. Transcript capture\n",
            "cursor": {},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")
    state = {}

    mock_agent_common.cmd_mock_fixture(state, "plain_question", freeze_static=True)

    assert state.get("pending", "") == "fixture"
    assert state.get("fixture_interactive", "") == ""
    capsys.readouterr()


def test_codex_startup_hook_warning_is_conditional(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "CODEX_BYPASS_HOOK_TRUST", False)
    monkeypatch.setattr(mock_agent_common, "CODEX_DANGER_FULL_ACCESS", False)
    mock_agent_common.print_codex_startup()
    output = capsys.readouterr().out
    assert "--dangerously-bypass-hook-trust" not in output
    assert "permissions: danger-full-access" not in output
    assert "permissions: YOLO mode" not in output
    assert "Mock" not in output
    assert "Tip: New Use /fast" in output

    monkeypatch.setattr(mock_agent_common, "CODEX_BYPASS_HOOK_TRUST", True)
    monkeypatch.setattr(mock_agent_common, "CODEX_DANGER_FULL_ACCESS", True)
    mock_agent_common.print_codex_startup()
    output = capsys.readouterr().out
    assert output.count("--dangerously-bypass-hook-trust") == 2
    assert "permissions: YOLO mode" in output
    assert "permissions: danger-full-access" not in output


def test_codex_tiny_tty_startup_drops_box_before_fixed_footer_scrolls(monkeypatch):
    class TtyBuffer(io.StringIO):
        def isatty(self):
            return True

        def fileno(self):
            raise OSError

    output = TtyBuffer()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "›")
    monkeypatch.setattr(mock_agent_common, "MODEL", "gpt-5.5")
    monkeypatch.setattr(mock_agent_common, "EFFORT", "xhigh")
    monkeypatch.setattr(mock_agent_common, "AGENT_PRODUCT_NAME", "OpenAI Codex")
    monkeypatch.setattr(mock_agent_common, "VERSION", "0.141.0")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 11)
    monkeypatch.setattr(mock_agent_common, "display_cwd", lambda: "~/yolomux.dev8002")

    mock_agent_common.print_codex_startup()
    mock_agent_common.render_live_composer("", 0, state={})

    rendered = output.getvalue()
    assert rendered.startswith("\x1b7\x1b[r\x1b8")
    assert "Tip: New Use /fast" not in rendered
    assert "╭" not in rendered and "╰" not in rendered
    assert "\x1b[H\x1b[J" not in rendered
    assert '\x1b[9;1H\x1b[2K› \x1b[2mExplain this codebase\x1b[0m' in rendered
    assert "\x1b[11;1H\x1b[2K  gpt-5.5 xhigh · ~/yolomux.dev8002" in rendered
    assert rendered.endswith("\x1b[9;3H\x1b7\x1b[1;7r\x1b8")


def test_codex_short_tty_startup_drops_tip_but_keeps_box(monkeypatch):
    class TtyBuffer(io.StringIO):
        def isatty(self):
            return True

        def fileno(self):
            raise OSError

    output = TtyBuffer()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "›")
    monkeypatch.setattr(mock_agent_common, "MODEL", "gpt-5.5")
    monkeypatch.setattr(mock_agent_common, "EFFORT", "xhigh")
    monkeypatch.setattr(mock_agent_common, "AGENT_PRODUCT_NAME", "OpenAI Codex")
    monkeypatch.setattr(mock_agent_common, "VERSION", "0.141.0")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    monkeypatch.setattr(mock_agent_common, "display_cwd", lambda: "~/yolomux.dev8002")

    mock_agent_common.print_codex_startup()
    mock_agent_common.render_live_composer("", 0, state={})

    rendered = output.getvalue()
    assert rendered.startswith("\x1b7\x1b[r\x1b8╭")
    assert "\x1b[H\x1b[J" not in rendered
    assert "Tip: New Use /fast" not in rendered
    assert "╭" in rendered and "╰" in rendered
    assert rendered.index("╰") < rendered.index("\x1b[9;1H\x1b[2K")
    assert '\x1b[10;1H\x1b[2K› \x1b[2mExplain this codebase\x1b[0m' in rendered
    assert "\x1b[12;1H\x1b[2K  gpt-5.5 xhigh · ~/yolomux.dev8002" in rendered
    assert rendered.endswith("\x1b[10;3H\x1b7\x1b[1;8r\x1b8")


def test_codex_compact_startup_clears_before_first_submitted_prompt(monkeypatch):
    class TtyBuffer(io.StringIO):
        def isatty(self):
            return True

        def fileno(self):
            raise OSError

    output = TtyBuffer()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "›")
    monkeypatch.setattr(mock_agent_common, "MODEL", "gpt-5.5")
    monkeypatch.setattr(mock_agent_common, "EFFORT", "xhigh")
    monkeypatch.setattr(mock_agent_common, "AGENT_PRODUCT_NAME", "OpenAI Codex")
    monkeypatch.setattr(mock_agent_common, "VERSION", "0.141.0")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    monkeypatch.setattr(mock_agent_common, "display_cwd", lambda: "~/yolomux.dev8002")
    state: dict[str, str] = {}

    mock_agent_common.print_codex_startup(state)
    assert state["codex_clear_startup_on_first_submit"] == "1"

    mock_agent_common.finish_live_composer("what time is it?", state)

    rendered = output.getvalue()
    clear_index = rendered.index("\x1b[1;1H\x1b[2K")
    prompt_index = rendered.rindex("› what time is it?")
    assert clear_index < prompt_index
    assert "codex_clear_startup_on_first_submit" not in state


def test_codex_prompt_and_working_text_match_current_cli(monkeypatch):
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "›")
    monkeypatch.setattr(mock_agent_common, "MODEL", "gpt-5.5")
    monkeypatch.setattr(mock_agent_common, "EFFORT", "xhigh")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "display_cwd", lambda: "~/yolomux.dev8002")

    prompt_display, status_display, _cursor_col = mock_agent_common.composer_render_parts("", 0)

    assert 'Explain this codebase' in prompt_display
    assert "Implement {feature}" not in prompt_display
    assert status_display.rstrip() == "  gpt-5.5 xhigh · ~/yolomux.dev8002"
    assert mock_agent_common.codex_working_line(3) == "• Working (3s • esc to interrupt)"

    working_lines = mock_agent_common.codex_working_block_lines(4)
    assert working_lines[0] == ""
    assert working_lines[1] == "• Working (4s • esc to interrupt)"
    assert working_lines[2] == ""
    assert working_lines[3] == ""
    assert 'Explain this codebase' in working_lines[4]
    assert working_lines[5] == ""
    assert working_lines[6].rstrip() == "  gpt-5.5 xhigh · ~/yolomux.dev8002"

    background_lines = mock_agent_common.codex_working_block_lines(4, "queued draft", 12, background=True)
    assert background_lines[1] == "• Working (4s • esc to interrupt) · 1 background terminal running · /ps to view · /stop to close"
    assert background_lines[4] == "› queued draft"
    assert "tab to queue message" in mock_agent_common.ANSI_RE.sub("", background_lines[6])

    queued_lines = mock_agent_common.codex_working_block_lines(
        24,
        queued_messages=[
            "in codex, when I type in a question, the bottom 2 lines disappear, and it says [thread]",
            "typing /model should give me the current model and available options",
        ],
    )
    visible_queued_lines = [mock_agent_common.ANSI_RE.sub("", line) for line in queued_lines]
    assert visible_queued_lines[1] == "• Working (24s • esc to interrupt)"
    assert visible_queued_lines[3] == "• Queued follow-up inputs"
    assert any(line.startswith("  ↳ in codex") for line in visible_queued_lines)
    assert any(line.startswith("  ↳ typing /model") for line in visible_queued_lines)
    assert "shift + ← edit last queued message" in "\n".join(visible_queued_lines)
    assert 'Explain this codebase' in visible_queued_lines[-3]
    assert visible_queued_lines[-1].rstrip() == "  gpt-5.5 xhigh · ~/yolomux.dev8002"

    typed_prompt, typed_status, typed_cursor_col = mock_agent_common.composer_render_parts("When I type, this is what it looks like.", 40)
    visible_status = mock_agent_common.ANSI_RE.sub("", typed_status)
    assert typed_prompt == "› When I type, this is what it looks like."
    assert typed_cursor_col == len(typed_prompt) + 1
    assert visible_status.rstrip() == "  gpt-5.5 xhigh · ~/yolomux.dev8002"


def test_codex_sleep_turn_uses_bottom_working_block(monkeypatch):
    class TtyBuffer(io.StringIO):
        def isatty(self):
            return True

    output = TtyBuffer()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "›")
    monkeypatch.setattr(mock_agent_common, "MODEL", "gpt-5.5")
    monkeypatch.setattr(mock_agent_common, "EFFORT", "xhigh")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    monkeypatch.setattr(mock_agent_common, "display_cwd", lambda: "~/yolomux.dev8002")
    monkeypatch.setattr(mock_agent_common.time, "sleep", lambda _seconds: None)
    state = {}

    mock_agent_common.handle_command("sleep 3", state)

    rendered = output.getvalue()
    assert "Would you like to run the following command?" not in rendered
    assert "• Running sleep 3 now." in rendered
    assert "\x1b[6;1H\x1b[2K" in rendered
    assert "\x1b[7;1H\x1b[2K\x1b[2m• \x1b[0m\x1b[1mWorking\x1b[0m\x1b[2m (" in rendered
    assert "1 background terminal running" in rendered
    assert "\x1b[8;1H\x1b[2K" in rendered
    assert "\x1b[9;1H\x1b[2K" in rendered
    assert "\x1b[10;1H\x1b[2K› \x1b[2mExplain this codebase\x1b[0m" in rendered
    assert "\x1b[11;1H\x1b[2K" in rendered
    assert "\x1b[12;1H\x1b[2K  gpt-5.5 xhigh · ~/yolomux.dev8002" in rendered
    assert "• Ran sleep 3" in rendered
    assert "  └ (no output)" in rendered
    assert "• Done." in rendered
    assert state.get("pending") != "permission"


def test_codex_permission_prompt_reserves_footer(monkeypatch):
    class TtyBuffer(io.StringIO):
        def isatty(self):
            return True

    output = TtyBuffer()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "›")
    monkeypatch.setattr(mock_agent_common, "SELECTOR_GLYPH", "›")
    monkeypatch.setattr(mock_agent_common, "MODEL", "gpt-5.5")
    monkeypatch.setattr(mock_agent_common, "EFFORT", "xhigh")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 90)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    monkeypatch.setattr(mock_agent_common, "display_cwd", lambda: "~/yolomux.dev8002")
    state = {}

    mock_agent_common.cmd_yesno(state, 1)

    rendered = output.getvalue()
    assert "• Mock build script — 1 step" in rendered
    assert "● Mock build script" not in rendered
    assert "\x1b[1;1H  Would you like to run the following command?" in rendered
    assert "\x1b[3;1H  $ mkdir -p build/output" in rendered
    assert "\x1b[5;1H› 1. Yes, proceed (y)" in rendered
    assert "\x1b[6;1H  2. Yes, and don't ask again" in rendered
    assert "\x1b[8;1H  Press enter to confirm or esc to cancel" in rendered
    assert "\x1b[9;1H\x1b[2K" in rendered
    assert "\x1b[10;1H\x1b[2K› \x1b[2mExplain this codebase\x1b[0m" in rendered
    assert "\x1b[11;1H\x1b[2K" in rendered
    assert "\x1b[12;1H\x1b[2K  gpt-5.5 xhigh · ~/yolomux.dev8002" in rendered
    assert "prompt_top_row" in state
    assert "prompt_bottom_row" in state


def test_codex_inline_composer_keeps_blank_line_before_status(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "›")
    monkeypatch.setattr(mock_agent_common, "MODEL", "gpt-5.5")
    monkeypatch.setattr(mock_agent_common, "EFFORT", "xhigh")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    monkeypatch.setattr(mock_agent_common, "display_cwd", lambda: "~/yolomux.dev8002")

    mock_agent_common.render_inline_composer("", 0)

    output = capsys.readouterr().out
    assert '› \x1b[2mExplain this codebase\x1b[0m\n\r\x1b[2K\n\r\x1b[2K  gpt-5.5 xhigh · ~/yolomux.dev8002' in output
    assert "\x1b[2A" in output


def test_codex_inline_composer_finish_preserves_status_row(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "›")
    monkeypatch.setattr(mock_agent_common, "MODEL", "gpt-5.5")
    monkeypatch.setattr(mock_agent_common, "EFFORT", "xhigh")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    monkeypatch.setattr(mock_agent_common, "display_cwd", lambda: "~/yolomux.dev8002")

    mock_agent_common.finish_inline_composer("What changed?")

    output = capsys.readouterr().out
    assert output == "\r\x1b[2K› What changed?\n\r\x1b[2K\n\r\x1b[2K"
    assert "gpt-5.5 xhigh" not in output


def test_codex_live_composer_renders_fixed_footer_region(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "›")
    monkeypatch.setattr(mock_agent_common, "MODEL", "gpt-5.5")
    monkeypatch.setattr(mock_agent_common, "EFFORT", "xhigh")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    monkeypatch.setattr(mock_agent_common, "display_cwd", lambda: "~/yolomux.dev8002")
    state = {}

    mock_agent_common.render_live_composer("draft", 5, state=state)

    output = capsys.readouterr().out
    assert "\x1b[9;1H\x1b[2K" in output
    assert "\x1b[10;1H\x1b[2K› draft" in output
    assert "\x1b[11;1H\x1b[2K" in output
    assert "\x1b[12;1H\x1b[2K  gpt-5.5 xhigh · ~/yolomux.dev8002" in output
    assert output.endswith("\x1b[10;8H\x1b7\x1b[1;8r\x1b8")
    assert state["live_composer_output_bottom"] == "8"


def test_codex_live_composer_finish_commits_prompt_above_fixed_footer(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "›")
    monkeypatch.setattr(mock_agent_common, "MODEL", "gpt-5.5")
    monkeypatch.setattr(mock_agent_common, "EFFORT", "xhigh")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    monkeypatch.setattr(mock_agent_common, "display_cwd", lambda: "~/yolomux.dev8002")
    state = {}

    mock_agent_common.finish_live_composer("What changed?", state)

    output = capsys.readouterr().out
    transcript_prefix = output.split("\x1b[9;1H", 1)[0]
    assert transcript_prefix == "\x1b[1;8r\x1b[8;1H\x1b[2K› What changed?\n\x1b[2K\n"
    assert "gpt-5.5 xhigh" not in transcript_prefix
    assert "\x1b[10;1H\x1b[2K› \x1b[2mExplain this codebase\x1b[0m" in output
    assert "\x1b[12;1H\x1b[2K  gpt-5.5 xhigh · ~/yolomux.dev8002" in output
    assert output.endswith("\x1b[1;8r\x1b[8;1H")
    assert state["live_composer_output_bottom"] == "8"


def test_empty_mock_input_is_noop(capsys):
    state = {}

    mock_agent_common.handle_command("", state)

    assert capsys.readouterr().out == ""
    assert state == {}


def test_codex_exit_footer_matches_resume_shape(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common.uuid, "uuid4", lambda: uuid.UUID("019eed20-f093-7622-9dbb-ff517b2ffc1c"))
    state = {}

    mock_agent_common.print_codex_exit_footer(state)

    output = capsys.readouterr().out
    assert "Token usage: total=23,456 input=22,552 (+ 66,688 cached) output=904 (reasoning 593)" in output
    assert "To continue this session, run codex resume 019eed20-f093-7622-9dbb-ff517b2ffc1c" in output


def test_codex_exit_footer_counts_wrapped_display_rows(monkeypatch):
    monkeypatch.setattr(mock_agent_common.uuid, "uuid4", lambda: uuid.UUID("019eed20-f093-7622-9dbb-ff517b2ffc1c"))
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    state = {}

    assert mock_agent_common.codex_exit_footer_display_line_count(state) == 3


def test_prepare_terminal_for_shell_clears_owned_footer_and_parks_cursor(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    state = {"prompt_top_row": "5"}

    mock_agent_common.prepare_terminal_for_shell(2, state)

    assert capsys.readouterr().out == "\x1b[r\x1b[5;1H\x1b[J\x1b[10;1H"


def test_live_composer_uses_row_above_bottom_status(monkeypatch):
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)

    assert mock_agent_common.live_composer_rows() == (10, 12)


def test_claude_composer_renders_separator_and_mode_line(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "claude")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "❯")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 24)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    state = {"claude_mode_index": "1"}

    mock_agent_common.render_live_composer("hello?", 6, state=state)

    output = capsys.readouterr().out
    separator = "\x1b[2m" + ("─" * 24) + "\x1b[0m"
    assert f"\x1b[9;1H\x1b[2K{separator}" in output
    assert f"\x1b[11;1H\x1b[2K{separator}" in output
    assert "\x1b[10;1H\x1b[2K❯ hello?" in output
    assert "⏵⏵ accept edits on" in output
    assert "Opus" not in output


def test_claude_default_composer_renders_startup_hint_and_status(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "claude")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "❯")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 24)

    mock_agent_common.render_live_composer("", 0, state={})

    output = capsys.readouterr().out
    assert '❯ \x1b[2mTry "fix typecheck errors"\x1b[0m' in output
    assert "tmux focus-events off" not in output
    assert "? for shortcuts · ← for agents" in output
    assert "accept edits on" not in output


def test_claude_live_composer_finish_preserves_footer_rows(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "claude")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "❯")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)

    mock_agent_common.finish_live_composer("help", state={})

    output = capsys.readouterr().out
    separator = "\x1b[2m" + ("─" * 80) + "\x1b[0m"
    assert "\x1b[1;8r\x1b[8;1H\x1b[2K❯ help\n" in output
    assert "tmux focus-events off" not in output
    assert f"\x1b[9;1H\x1b[2K{separator}" in output
    assert '❯ \x1b[2mTry "fix typecheck errors"\x1b[0m' in output
    assert f"\x1b[11;1H\x1b[2K{separator}" in output
    assert "? for shortcuts · ← for agents" in output
    assert output.endswith("\x1b[1;8r\x1b[8;1H")


def test_claude_permission_approval_preserves_footer_before_work(monkeypatch):
    class TtyBuffer(io.StringIO):
        def isatty(self):
            return True

    output = TtyBuffer()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "claude")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "❯")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    monkeypatch.setattr(mock_agent_common, "run_with_agent_working_status", lambda _command, _use_real: ("(No output)", 2))
    state = {
        "pending": "permission",
        "command": "sleep 2",
        "prompt_top_row": "2",
        "prompt_bottom_row": "7",
    }

    mock_agent_common.approve_pending_permission(state)

    rendered = output.getvalue()
    footer_index = rendered.index('❯ \x1b[2mTry "fix typecheck errors"\x1b[0m')
    approved_index = rendered.index("● User approved Claude's request")
    assert footer_index < approved_index
    assert "\x1b[1;8r\x1b[8;1H" in rendered
    assert "tmux focus-events off" not in rendered
    assert "? for shortcuts · ← for agents" in rendered
    assert "● Bash(sleep 2)" in rendered


def test_claude_question_mark_renders_shortcuts_overlay(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "claude")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "❯")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 120)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 30)

    mock_agent_common.render_live_composer("", 0, state={"claude_shortcuts_visible": "1"})

    output = capsys.readouterr().out
    assert '❯ \x1b[2mTry "fix typecheck errors"\x1b[0m' in output
    assert "! for shell mode" in output
    assert "double tap esc to clear input" in output
    assert "\\⏎ for newline" in output
    assert "/keybindings to customize" in output
    assert "tmux focus-events off" not in output


def test_claude_composer_omits_separators_in_tiny_pane(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "claude")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "❯")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 24)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 5)

    mock_agent_common.render_live_composer("hello?", 6, state={})

    output = capsys.readouterr().out
    assert "─" not in output
    assert "\x1b[4;1H\x1b[2K❯ hello?" in output
    assert "\x1b[5;1H\x1b[2K" in output


def test_claude_shift_tab_mode_cycle():
    state = {}

    assert mock_agent_common.claude_mode_status_line(state) == ""
    mock_agent_common.cycle_claude_mode(state)
    assert mock_agent_common.claude_mode_status_line(state) == "  ⏵⏵ accept edits on (shift+tab to cycle) · ← for agents"
    mock_agent_common.cycle_claude_mode(state)
    assert mock_agent_common.claude_mode_status_line(state) == "  ⏸ plan mode on (shift+tab to cycle) · ← for agents"
    mock_agent_common.cycle_claude_mode(state)
    assert mock_agent_common.claude_mode_status_line(state) == "  ⏵⏵ auto mode on (shift+tab to cycle) · ← for agents"
    mock_agent_common.cycle_claude_mode(state)
    assert mock_agent_common.claude_mode_status_line(state) == ""


def test_ctrl_c_requires_second_press_to_exit():
    state = {}

    assert mock_agent_common.ctrl_c_requests_exit(state, now=10.0) is False
    assert mock_agent_common.ctrl_c_requests_exit(state, now=11.0) is True
    mock_agent_common.clear_ctrl_c_exit_window(state)
    assert mock_agent_common.ctrl_c_requests_exit(state, now=20.0) is False
    assert mock_agent_common.ctrl_c_requests_exit(state, now=23.0) is True


def test_terminal_width_respects_narrow_pane(monkeypatch):
    monkeypatch.setattr(os, "get_terminal_size", lambda *a, **k: os.terminal_size((70, 24)))
    # Must not inflate a real narrow pane to a wider floor — that is what wrapped the box.
    assert mock_agent_common.terminal_width() <= 70


def test_claude_startup_uses_compact_header_in_home_and_project(monkeypatch):
    calls = []
    monkeypatch.setattr(mock_agent_common, "print_welcome_box", lambda: calls.append("box"))
    monkeypatch.setattr(mock_agent_common, "print_minimal_header", lambda: calls.append("min"))
    monkeypatch.setattr(mock_agent_common, "print_prompt_box", lambda *a, **k: None)
    monkeypatch.setattr(mock_agent_common, "STARTUP_STYLE", "default")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 120)

    monkeypatch.setattr(os, "getcwd", lambda: os.path.expanduser("~"))
    mock_agent_common.print_startup()
    monkeypatch.setattr(os, "getcwd", lambda: "/tmp/yolomux-project-dir")
    mock_agent_common.print_startup()

    assert calls == ["min", "min"]


def test_claude_startup_clears_tiny_tty_without_overwritten_header(monkeypatch):
    class TtyBuffer(io.StringIO):
        def isatty(self):
            return True

    output = TtyBuffer()
    state = {}
    calls = []
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(mock_agent_common, "STARTUP_STYLE", "default")
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 5)
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 120)
    monkeypatch.setattr(mock_agent_common, "print_minimal_header", lambda: calls.append("min"))

    mock_agent_common.print_startup(state)

    assert output.getvalue() == "\x1b[r\x1b[H\x1b[J"
    assert calls == []
    assert state["claude_startup_header_pending"] == "1"


def test_claude_startup_redraws_header_after_tiny_tty_grows(monkeypatch):
    class TtyBuffer(io.StringIO):
        def isatty(self):
            return True

    output = TtyBuffer()
    state = {"claude_startup_header_pending": "1"}
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "default")
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 20)
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)

    mock_agent_common.render_pending_claude_startup_header(state)

    assert "\x1b[r" in output.getvalue()
    assert "Claude Code" in output.getvalue()
    assert "\x1b[1;1H" in output.getvalue()
    assert "\x1b[2;1H" in output.getvalue()
    assert "\x1b[3;1H" in output.getvalue()
    assert "claude_startup_header_pending" not in state
    assert state["claude_startup_header_visible"] == "1"


@pytest.mark.e2e
@pytest.mark.socket
@pytest.mark.parametrize("case", root_inventory_cases(), ids=lambda case: case["inventory"]["id"])
def test_mockcase_prompt_corpus_families_classify_like_inventory(monkeypatch, tmp_path, case):
    try:
        session = run_mockcase(monkeypatch, tmp_path, case)
        expected = case["inventory"]["expected"]
        state = classify_mockcase_until(
            {"pane_target": f"{session}:", "agent_kind": expected.get("agent") or case_agent(case)},
            expected["screen_key"],
            session=session,
            prompt_source="pane",
            include_composer=False,
            include_transcript_activity=False,
        )
        assert state.screen["key"] == expected["screen_key"], state.capture.visible_text
        if expected["ask"]:
            assert state.display["prompt_kind"] == expected["prompt_kind"]
            assert state.display["question_text"] == expected["question_text"]
            assert state.display["selected_option"] == expected["selected_option"]
            assert [item["label"] for item in state.display["options"]] == expected["option_labels"]
            assert state.approval["approval_visible"] is expected["approval_visible"]
            assert state.approval["command"] == (expected["command"] or "")
        else:
            assert state.approval["approval_visible"] is False
    finally:
        cleanup_mockcase(tmp_path)


@pytest.mark.e2e
@pytest.mark.socket
@pytest.mark.parametrize("case", promoted_capture_cases(), ids=lambda case: case["inventory"]["id"])
def test_mockcase_promoted_captures_classify_like_real_capture(monkeypatch, tmp_path, case):
    try:
        session = run_mockcase(monkeypatch, tmp_path, case)
        expected = case["data"]["expected_promoted"]
        state = classify_mockcase_until(
            {"pane_target": f"{session}:", "agent_kind": case_agent(case)},
            expected["screen_key"],
            session=session,
            prompt_source="pane",
            include_composer=True,
            include_transcript_activity=False,
            capture_full_for_bash=False,
            expected_composer_key=expected["composer_key"],
        )
        assert state.agent_kind == expected["agent_kind"]
        assert state.screen["key"] == expected["screen_key"], state.capture.visible_text
        assert state.reason_code == expected["reason_code"]
        assert state.attention_kind == expected["attention_kind"]
        assert state.attention_label == expected["attention_label"]
        assert state.approval["approval_visible"] is expected["approval_visible"]
        assert state.approval["approval_type"] == expected["approval_type"]
        assert state.composer.key == expected["composer_key"]
    finally:
        cleanup_mockcase(tmp_path)
