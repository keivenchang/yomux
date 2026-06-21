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
        if line in {'› Implement {feature}', '❯ Try "fix typecheck errors"'}:
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
        elif "Implement {feature}" not in last and "Try \"fix typecheck errors\"" not in last:
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
        f"cd {REPO_ROOT} && exec python3 tools/mock_{agent}.py",
    )
    assert created.returncode == 0, created.stderr or created.stdout
    try:
        rendered, pane = wait_for_mockcase_render(tmux_binary, socket_path, session, "Try \"fix typecheck errors\"" if agent == "claude" else "Implement {feature}")
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
        },
        {
            "agent": "claude",
            "case_name": "synthetic_plan",
            "path": PROMPT_CORPUS_DIR / "synthetic" / "synthetic_plan.yaml",
            "cursor": {},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")

    mock_agent_common.print_mock_fixture_list()

    output = capsys.readouterr().out
    assert "codex: choice_question" in output
    assert "cursor=2,37" in output
    assert "claude: synthetic_plan" not in output


def test_mocklist_alias_prints_fixture_list(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "choice_question",
            "path": PROMPT_CORPUS_DIR / "captures" / "choice_question.yaml",
            "cursor": {"x": 2, "y": 37},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")

    mock_agent_common.handle_command("mocklist", {})

    output = capsys.readouterr().out
    assert "Mock fixture cases" in output
    assert "choice_question" in output


def test_mock_list_filters_to_current_agent_and_shared_cases(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "codex_case",
            "path": PROMPT_CORPUS_DIR / "captures" / "codex_case.yaml",
            "cursor": {"x": 2, "y": 37},
        },
        {
            "agent": "claude",
            "case_name": "claude_case",
            "path": PROMPT_CORPUS_DIR / "captures" / "claude_case.yaml",
            "cursor": {"x": 3, "y": 34},
        },
        {
            "agent": "unknown",
            "case_name": "unknown_case",
            "path": PROMPT_CORPUS_DIR / "synthetic" / "unknown_case.yaml",
            "cursor": {},
        },
        {
            "agent": "generic",
            "case_name": "generic_case",
            "path": PROMPT_CORPUS_DIR / "synthetic" / "generic_case.yaml",
            "cursor": {},
        },
    ])

    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")
    mock_agent_common.handle_command("mock list", {})
    codex_output = capsys.readouterr().out
    assert "codex: codex_case" in codex_output
    assert "unknown: unknown_case" in codex_output
    assert "generic: generic_case" in codex_output
    assert "claude: claude_case" not in codex_output

    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "claude")
    mock_agent_common.handle_command("mock list", {})
    claude_output = capsys.readouterr().out
    assert "claude: claude_case" in claude_output
    assert "unknown: unknown_case" in claude_output
    assert "generic: generic_case" in claude_output
    assert "codex: codex_case" not in claude_output


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
    mock_agent_common.print_codex_startup()
    output = capsys.readouterr().out
    assert "--dangerously-bypass-hook-trust" not in output

    monkeypatch.setattr(mock_agent_common, "CODEX_BYPASS_HOOK_TRUST", True)
    mock_agent_common.print_codex_startup()
    output = capsys.readouterr().out
    assert "--dangerously-bypass-hook-trust" in output


def test_live_composer_uses_row_above_bottom_status(monkeypatch):
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)

    assert mock_agent_common.live_composer_rows() == (10, 12)


def test_ctrl_c_requires_second_press_to_exit():
    state = {}

    assert mock_agent_common.ctrl_c_requests_exit(state, now=10.0) is False
    assert mock_agent_common.ctrl_c_requests_exit(state, now=11.0) is True
    mock_agent_common.clear_ctrl_c_exit_window(state)
    assert mock_agent_common.ctrl_c_requests_exit(state, now=20.0) is False
    assert mock_agent_common.ctrl_c_requests_exit(state, now=23.0) is True


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
