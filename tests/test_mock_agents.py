import io
import os
from pathlib import Path
import re
import shlex
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
VISUAL_TMUX_SESSION_PREFIX = "test-mock-visual"


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
        "78",
        "-y",
        "35",
        f"cd {REPO_ROOT} && exec python3 tools/{agent}.py --mock",
    )
    assert created.returncode == 0, created.stderr or created.stdout
    try:
        prompt_text = 'Try "fix typecheck errors"' if agent == "claude" else "Explain this codebase"
        rendered, pane = wait_for_mockcase_render(tmux_binary, socket_path, session, prompt_text)
        assert rendered, pane
        tmux_cmd(tmux_binary, socket_path, "send-keys", "-t", f"{session}:", f"fixture {case_command_name(case)}", "Enter")
        rendered, pane = wait_for_mockcase_render(tmux_binary, socket_path, session, case["text"])
        assert rendered, f"{case['path'].name} did not render through fixture:\n{pane}"
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


def visual_tmux_socket_path():
    worker = re.sub(r"[^A-Za-z0-9_.-]", "-", os.environ.get("PYTEST_XDIST_WORKER", "main"))
    socket_base = Path("/tmp") / f"yolomux-{VISUAL_TMUX_SESSION_PREFIX}-{os.getuid()}-{worker}"
    socket_base.mkdir(mode=0o700, parents=True, exist_ok=True)
    return socket_base / "s"


def cleanup_visual_test_sessions(tmux_binary, socket_path):
    listed = tmux_cmd(tmux_binary, socket_path, "list-sessions", "-F", "#{session_name}", timeout=3)
    if listed.returncode != 0:
        return
    for session in listed.stdout.splitlines():
        if session.startswith("test-"):
            tmux_cmd(tmux_binary, socket_path, "kill-session", "-t", session, timeout=3)


class VisualTmuxHarness:
    def __init__(self, tmux_binary, socket_path):
        self.tmux_binary = tmux_binary
        self.socket_path = socket_path
        self.sessions = []

    def launch(self, purpose, command, *, width=103, height=58):
        session = f"{VISUAL_TMUX_SESSION_PREFIX}-{purpose}-{uuid.uuid4().hex[:8]}"
        shell_command = f"cd {shlex.quote(str(REPO_ROOT))} && exec {shlex.join(command)}"
        created = tmux_cmd(
            self.tmux_binary,
            self.socket_path,
            "new-session",
            "-d",
            "-s",
            session,
            "-x",
            str(width),
            "-y",
            str(height),
            shell_command,
            timeout=8,
        )
        assert created.returncode == 0, created.stderr or created.stdout
        self.sessions.append(session)
        return session

    def capture(self, session, *, scrollback=False, join_wrapped=False):
        command = ["capture-pane", "-p", "-t", f"{session}:"]
        if join_wrapped:
            command.append("-J")
        if scrollback:
            command.extend(["-S", "-2000"])
        return tmux_cmd(self.tmux_binary, self.socket_path, *command, timeout=5).stdout or ""

    def send_keys(self, session, *keys):
        sent = tmux_cmd(self.tmux_binary, self.socket_path, "send-keys", "-t", f"{session}:", *keys, timeout=5)
        assert sent.returncode == 0, sent.stderr or sent.stdout

    def resize(self, session, *, width, height):
        resized = tmux_cmd(
            self.tmux_binary,
            self.socket_path,
            "resize-window",
            "-t",
            f"{session}:",
            "-x",
            str(width),
            "-y",
            str(height),
            timeout=5,
        )
        assert resized.returncode == 0, resized.stderr or resized.stdout

    def wait_until(self, session, predicate, *, timeout=15):
        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            last = self.capture(session)
            if predicate(last):
                return True, last
            time.sleep(0.2)
        return False, last


@pytest.fixture
def visual_tmux(monkeypatch):
    tmux_binary = shutil.which("tmux")
    if not tmux_binary:
        pytest.skip("tmux is not installed")
    socket_path = visual_tmux_socket_path()
    cleanup_visual_test_sessions(tmux_binary, socket_path)
    monkeypatch.setenv(YOLOMUX_TMUX_SOCKET_ENV, str(socket_path))
    harness = VisualTmuxHarness(tmux_binary, socket_path)
    try:
        yield harness
    finally:
        for session in reversed(harness.sessions):
            tmux_cmd(tmux_binary, socket_path, "kill-session", "-t", session, timeout=3)
        cleanup_visual_test_sessions(tmux_binary, socket_path)
        tmux_cmd(tmux_binary, socket_path, "kill-server", timeout=3)
        shutil.rmtree(socket_path.parent, ignore_errors=True)


def extract_first_box(pane):
    lines = pane.splitlines()
    for start, line in enumerate(lines):
        if line.startswith("╭"):
            for end in range(start + 1, len(lines)):
                if lines[end].startswith("╰"):
                    return lines[start:end + 1]
    return []


def assert_no_startup_ellipsis(box):
    startup_text = "\n".join(box)
    assert "..." not in startup_text
    assert "…" not in startup_text


def expected_display_cwd(path):
    cwd = os.fspath(path)
    home = os.path.expanduser("~")
    if cwd == home:
        return "~"
    if cwd.startswith(home + os.sep):
        return "~" + cwd[len(home):]
    return cwd


def expected_codex_startup_box(model, effort, directory):
    rows = [
        " >_ OpenAI Codex (v0.142.0)",
        "",
        f" model:     {model} {effort}   /model to change",
        f" directory: {directory}",
    ]
    inner = max(45, *(len(row) for row in rows))
    return ["╭" + ("─" * inner) + "╮", *(f"│{row:<{inner}}│" for row in rows), "╰" + ("─" * inner) + "╯"]


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
    assert "choice_question [ASK?] choice_question.yaml cursor=2,37" in output
    assert "cursor=2,37" in output
    assert "synthetic_plan [idle]" not in output


def test_all_claude_prompt_corpus_fixtures_have_cursor_metadata(monkeypatch):
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", None)
    missing = [
        f"{case['case_name']}:{case['path'].relative_to(REPO_ROOT)}"
        for case in mock_agent_common.load_mock_fixture_cases()
        if case.get("agent") == "claude" and mock_agent_common.mock_fixture_cursor_label(case.get("cursor") or {}) == "cursor=missing"
    ]

    assert missing == []


def test_mock_fixture_list_prints_full_rows_for_terminal_wrap(monkeypatch, capsys):
    file_name = "interrupted_what_should_claude_do_instead__claude-code-2.1.185_20260621.yaml"
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "claude",
            "case_name": "interrupted_what_should_claude_do_instead",
            "path": PROMPT_CORPUS_DIR / "captures" / file_name,
            "cursor": {},
            "expected": {"attention_label": "ASK?", "screen_key": "needs-input"},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "claude")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 72)

    mock_agent_common.print_mock_fixture_list(include_idle=True)

    output = capsys.readouterr().out
    lines = output.splitlines()
    assert any(line.startswith("  ⎿  interrupted_what_should_claude_do_instead [ASK?]") for line in lines)
    assert file_name in output
    assert "…" not in output


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


def test_mockcase_and_case_aliases_are_not_commands(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")

    mock_agent_common.handle_command("mockcase choice_question", {})
    mock_agent_common.handle_command("case choice_question", {})

    output = capsys.readouterr().out
    assert output.count("I don't know how to handle") == 2
    assert "mockcase" not in "\n".join(line for line in output.splitlines() if "Built-in actions" in line or "fixture <case>" in line)
    assert "fixture <case>" in output


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
    assert "codex_question_case [ASK?] codex_question_case.yaml" in codex_output
    assert "codex_idle_case" not in codex_output
    assert "unknown_case" not in codex_output
    assert "generic_case" not in codex_output
    assert "claude_approval_case" not in codex_output

    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "claude")
    mock_agent_common.handle_command("mock list", {})
    claude_output = capsys.readouterr().out
    assert "claude_approval_case [YOLO?] claude_approval_case.yaml" in claude_output
    assert "claude_idle_case" not in claude_output
    assert "unknown_case" not in claude_output
    assert "generic_case" not in claude_output
    assert "codex_question_case" not in claude_output

    mock_agent_common.handle_command("mock list all", {})
    all_output = capsys.readouterr().out
    assert "claude_idle_case [idle] claude_case.yaml" in all_output
    assert "claude_approval_case [YOLO?] claude_approval_case.yaml" in all_output
    assert "unknown_case [ASK?] unknown_case.yaml" in all_output
    assert "generic_case [RUN] generic_case.yaml" in all_output
    assert "codex_question_case" not in all_output

    mock_agent_common.handle_command("mock list idle", {})
    idle_output = capsys.readouterr().out
    assert "claude_idle_case [idle] claude_case.yaml" in idle_output
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
    assert "synthetic_only_case [YOLO?] synthetic_only_case.yaml" in output
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
    plain_rendered = mock_agent_common.ANSI_RE.sub("", rendered)
    assert "Working" in plain_rendered
    assert "Pursuing goal" not in plain_rendered


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
    assert "codex_working_pause_until" not in state
    assert "Improve documentation in @filename" not in first_render
    assert "• Working (0s • esc to interrupt)" in mock_agent_common.ANSI_RE.sub("", first_render)


def test_codex_working_refresh_updates_only_status_row_on_steady_tick(monkeypatch):
    class TtyBuffer(io.StringIO):
        def isatty(self):
            return True

    output = TtyBuffer()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 120)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")

    start_row, _line_count, render_key = mock_agent_common.refresh_codex_working_block(81)
    output.truncate(0)
    output.seek(0)
    mock_agent_common.refresh_codex_working_block(
        81.12,
        previous_key=render_key,
        previous_start_row=start_row,
    )

    rendered = output.getvalue()
    working_row = start_row + 1
    prompt_row = start_row + 4
    assert "Working" in mock_agent_common.ANSI_RE.sub("", rendered)
    assert f"\x1b[{working_row};1H\x1b[2K" in rendered
    assert f"\x1b[{prompt_row};1H\x1b[2K" not in rendered


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
    assert rendered.count('Try "fix typecheck errors"') == 0


def test_claude_working_line_increments_from_capture(monkeypatch):
    state = {
        "claude_working_base_seconds": "11",
        "claude_working_base_tokens": "471",
        "claude_working_marker": "·",
        "claude_working_verb": "Clauding",
    }

    assert mock_agent_common.claude_working_line(11, state) == "· Clauding… (11s · ↓ 471 tokens)"
    assert mock_agent_common.claude_working_line(11 + mock_agent_common.CLAUDE_WORKING_FRAME_SECONDS, state) == "✢ Clauding… (11s · ↓ 473 tokens)"
    assert mock_agent_common.claude_working_line(11 + mock_agent_common.CLAUDE_WORKING_FRAME_SECONDS * 5, state) == "* Clauding… (11s · ↓ 485 tokens)"
    assert mock_agent_common.claude_working_line(13, state) == "✽ Clauding… (13s · ↓ 519 tokens)"


def test_claude_working_composer_omits_idle_suggestion_until_text(monkeypatch):
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "claude")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "❯")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    state = {"claude_working": "1", "claude_working_footer_status": "  ⏸ plan mode on (shift+tab to cycle) · esc to interrupt"}

    prompt_display, status_display, cursor_col = mock_agent_common.composer_render_parts("", 0, state=state)
    typed_prompt, _typed_status, typed_cursor_col = mock_agent_common.composer_render_parts("queued input", 12, state=state)

    assert prompt_display == "❯ "
    assert 'Try "fix typecheck errors"' not in prompt_display
    assert status_display.rstrip() == "  ⏸ plan mode on (shift+tab to cycle) · esc to interrupt"
    assert cursor_col == 3
    assert typed_prompt == "❯ queued input"
    assert typed_cursor_col == 15


def test_claude_working_frame_clears_previous_owned_row(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "claude")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "❯")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    state = {
        "claude_working_row": "6",
        "claude_working_base_seconds": "11",
        "claude_working_base_tokens": "471",
        "claude_working_marker": "·",
        "claude_working_verb": "Clauding",
        "claude_working_footer_status": "  ⏸ plan mode on (shift+tab to cycle) · esc to interrupt",
    }

    working_row = mock_agent_common.write_claude_working_block(11, state)

    output = capsys.readouterr().out
    for row in range(6, 13):
        assert f"\x1b[{row};1H\x1b[2K" in output
    assert "\x1b7\x1b[8;1H\x1b[2K· Clauding… (11s · ↓ 471 tokens)" in output
    assert working_row == 8
    assert state["claude_working_row"] == "8"


def test_clear_claude_working_block_erases_owned_footer_rows(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "claude")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "❯")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    state = {
        "claude_working_base_seconds": "11",
        "claude_working_base_tokens": "471",
        "claude_working_marker": "·",
        "claude_working_verb": "Clauding",
        "claude_working_footer_status": "  ⏸ plan mode on (shift+tab to cycle) · esc to interrupt",
    }

    working_row = mock_agent_common.write_claude_working_block(11, state)
    capsys.readouterr()
    mock_agent_common.clear_claude_working_block(state, working_row)

    output = capsys.readouterr().out
    for row in range(8, 13):
        assert f"\x1b[{row};1H\x1b[2K" in output
    assert output.count('❯ \x1b[2mTry "fix typecheck errors"\x1b[0m') == 1
    assert "Clauding" not in output
    assert "shift+tab to cycle" not in output
    assert "? for shortcuts · ← for agents" in output
    assert state.get("claude_working") is None


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
            "cursor": {"x": 6, "y": 0},
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
    assert "file: codex_case.yaml" not in output
    assert f"path: {PROMPT_CORPUS_DIR / 'captures' / 'codex_case.yaml'}" in output
    assert "codex_case.yaml" in output
    assert "cursor: x=6 y=0 shown=x=6 y=0 (0-based)" in output
    assert "----- capture (source 200x40; rendered 200x40; cursor marked) -----\ncodex visible text\n      ^ cursor\n" in output
    assert "===== END FIXTURE: codex_case.yaml =====" in output
    assert "shared_idle.yaml" in output
    assert "cursor: missing" in output
    assert "shared idle text" in output
    assert "claude_case.yaml" not in output
    assert "claude visible text" not in output


def test_fixture_dump_clips_overwide_rule_rows():
    dump = mock_agent_common.format_fixture_capture_for_dump("─" * 200, {}, cols=78, source_cols=200)

    assert dump["text"] == ("─" * 78) + "\n"


def test_fixture_dump_preserves_canonical_width_by_default():
    dump = mock_agent_common.format_fixture_capture_for_dump("─" * 200, {}, source_cols=200)

    assert dump["text"] == ("─" * 200) + "\n"


def test_fixture_dump_marks_numbered_cursor_with_caret_row():
    dump = mock_agent_common.format_fixture_capture_for_dump(
        "Do you want to proceed?\n  1. Yes\n  2. No",
        {"x": 2, "y": 1},
        source_cols=200,
    )

    assert dump["text"] == "Do you want to proceed?\n  1. Yes\n  ^ cursor\n  2. No\n"


def test_plain_mock_stretches_capture_width_chrome(monkeypatch, capsys):
    content_row = "│ body" + (" " * 71) + "│"
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "wide_chrome",
            "keys": {"wide_chrome"},
            "path": PROMPT_CORPUS_DIR / "captures" / "wide_chrome.yaml",
            "styled_capture": "\n".join([
                "─" * 78,
                "╭" + ("─" * 76) + "╮",
                content_row,
                "╰" + ("─" * 76) + "╯",
            ]),
            "cursor": {},
            "width": 78,
            "height": 35,
            "expected": {"screen_key": "idle"},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 90)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 20)
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    state = {}

    mock_agent_common.cmd_mock_fixture(state, "wide_chrome", freeze_static=False)

    output = capsys.readouterr().out
    assert ("─" * 90) in output
    assert ("╭" + ("─" * 88) + "╮") in output
    assert ("│ body" + (" " * 83) + "│") in output
    assert ("╰" + ("─" * 88) + "╯") in output


def test_plain_mock_shrinks_wide_capture_width_chrome(monkeypatch, capsys):
    content_row = "│ body" + (" " * 193) + "│"
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "wide_chrome",
            "keys": {"wide_chrome"},
            "path": PROMPT_CORPUS_DIR / "captures" / "wide_chrome.yaml",
            "styled_capture": "\n".join([
                "─" * 200,
                "╭" + ("─" * 198) + "╮",
                content_row,
                "╰" + ("─" * 198) + "╯",
            ]),
            "cursor": {},
            "width": 200,
            "height": 40,
            "expected": {"screen_key": "idle"},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 78)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 20)
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    state = {}

    mock_agent_common.cmd_mock_fixture(state, "wide_chrome", freeze_static=False)

    output = capsys.readouterr().out
    assert ("─" * 78) in output
    assert ("╭" + ("─" * 76) + "╮") in output
    assert ("│ body" + (" " * 71) + "│") in output
    assert ("╰" + ("─" * 76) + "╯") in output


def test_mockcase_preserves_capture_width_chrome(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "wide_chrome",
            "keys": {"wide_chrome"},
            "path": PROMPT_CORPUS_DIR / "captures" / "wide_chrome.yaml",
            "styled_capture": "─" * 78,
            "cursor": {},
            "width": 78,
            "height": 35,
            "expected": {"screen_key": "idle"},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 90)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 20)
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    state = {}

    mock_agent_common.cmd_mock_fixture(state, "wide_chrome", freeze_static=True)

    output = capsys.readouterr().out
    assert ("─" * 78) in output
    assert ("─" * 90) not in output


def test_mockcase_rerenders_wide_capture_to_narrow_terminal(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "wide_chrome",
            "keys": {"wide_chrome"},
            "path": PROMPT_CORPUS_DIR / "captures" / "wide_chrome.yaml",
            "styled_capture": "─" * 200,
            "cursor": {},
            "width": 200,
            "height": 40,
            "expected": {"screen_key": "idle"},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 90)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 20)
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    state = {}

    mock_agent_common.cmd_mock_fixture(state, "wide_chrome", freeze_static=True)

    output = capsys.readouterr().out
    assert ("─" * 90) in output
    assert ("─" * 200) not in output


def test_plain_mock_reconstructs_hard_wrapped_fixture_text(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "wrapped_text",
            "keys": {"wrapped_text"},
            "path": PROMPT_CORPUS_DIR / "captures" / "wrapped_text.yaml",
            "styled_capture": "\n".join([
                "› Ask me this question and wait for my answer instead of choosing yourself: Wh",
                "ich YOLOmux verifier mode should we use?",
                "  1. Pane capture",
                "  2. Transcript capture",
            ]),
            "cursor": {},
            "width": 78,
            "height": 35,
            "expected": {"screen_key": "idle"},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 120)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 20)
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    state = {}

    mock_agent_common.cmd_mock_fixture(state, "wrapped_text", freeze_static=False)

    output = capsys.readouterr().out
    assert "Which YOLOmux verifier mode should we use?" in output
    assert "Wh\nich" not in output


def test_mockcase_preserves_hard_wrapped_fixture_text(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "MOCK_FIXTURE_CASES", [
        {
            "agent": "codex",
            "case_name": "wrapped_text",
            "keys": {"wrapped_text"},
            "path": PROMPT_CORPUS_DIR / "captures" / "wrapped_text.yaml",
            "styled_capture": "\n".join([
                "› Ask me this question and wait for my answer instead of choosing yourself: Wh",
                "ich YOLOmux verifier mode should we use?",
            ]),
            "cursor": {},
            "width": 78,
            "height": 35,
            "expected": {"screen_key": "idle"},
        },
    ])
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 120)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 20)
    monkeypatch.setattr(mock_agent_common, "AGENT_NAME", "codex")
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    state = {}

    mock_agent_common.cmd_mock_fixture(state, "wrapped_text", freeze_static=True)

    output = capsys.readouterr().out
    assert "Wh\nich YOLOmux verifier mode should we use?" in output


def test_fixture_reconstruct_does_not_merge_options_or_fresh_rows():
    line = "A" * 78
    rendered = mock_agent_common.rerender_fixture_lines_for_width(
        [line, "  1. Pane capture", "• Fresh assistant row"],
        120,
        78,
    )

    assert rendered == [line, "  1. Pane capture", "• Fresh assistant row"]


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
    monkeypatch.setattr(mock_agent_common, "VERSION", "0.142.0")
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
    monkeypatch.setattr(mock_agent_common, "VERSION", "0.142.0")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    monkeypatch.setattr(mock_agent_common, "display_cwd", lambda: "~/yolomux.dev8002")
    state: dict[str, str] = {}

    mock_agent_common.print_codex_startup(state)
    assert state["codex_startup_inline_composer"] == "1"
    mock_agent_common.render_live_composer("", 0, state=state)

    rendered = output.getvalue()
    assert rendered.startswith("\x1b7\x1b[r\x1b8╭")
    assert "\x1b[H\x1b[J" not in rendered
    assert "Tip: New Use /fast" not in rendered
    assert "╭" in rendered and "╰" in rendered
    assert "Explain this codebase" not in rendered
    assert "\x1b[9;1H\x1b[2K" not in rendered
    assert state["codex_startup_inline_composer"] == "1"


def test_codex_tty_startup_reserves_footer_rows_after_tip(monkeypatch):
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
    monkeypatch.setattr(mock_agent_common, "VERSION", "0.142.0")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 103)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 58)
    monkeypatch.setattr(mock_agent_common, "display_cwd", lambda: "~/yolomux.dev8002")
    state: dict[str, str] = {}

    mock_agent_common.print_codex_startup(state)
    mock_agent_common.render_live_composer("", 0, state=state)

    rendered = output.getvalue()
    footer_top = mock_agent_common.live_composer_footer_top("", False, state)
    footer_clear = f"\x1b[{footer_top};1H\x1b[2K"
    reserve_gap = rendered.split("Tip: New Use /fast", 1)[1].split(footer_clear, 1)[0]
    assert "│ >_ OpenAI Codex (v0.142.0)                  │" in rendered
    assert "│                                             │" in rendered
    assert "│ model:     gpt-5.5 xhigh   /model to change │" in rendered
    assert "│ directory: ~/yolomux.dev8002                │" in rendered
    assert "╰─────────────────────────────────────────────╯" in rendered
    assert footer_clear in rendered
    assert reserve_gap.count("\n") >= len(mock_agent_common.codex_composer_footer_lines()) + 1


def test_codex_startup_expands_for_long_model_without_ellipsis(monkeypatch):
    class TtyBuffer(io.StringIO):
        def isatty(self):
            return True

        def fileno(self):
            raise OSError

    output = TtyBuffer()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    monkeypatch.setattr(mock_agent_common, "MODEL", "gpt-5.4-mini")
    monkeypatch.setattr(mock_agent_common, "EFFORT", "medium")
    monkeypatch.setattr(mock_agent_common, "AGENT_PRODUCT_NAME", "OpenAI Codex")
    monkeypatch.setattr(mock_agent_common, "VERSION", "0.142.0")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 103)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 58)
    monkeypatch.setattr(mock_agent_common, "display_cwd", lambda: "~/yolomux.dev8002")

    mock_agent_common.print_codex_startup({})

    rendered = output.getvalue()
    assert "…" not in rendered
    assert " model:     gpt-5.4-mini medium   /model to change" in rendered


def test_codex_compact_startup_preserves_box_before_first_submitted_prompt(monkeypatch):
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
    monkeypatch.setattr(mock_agent_common, "VERSION", "0.142.0")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    monkeypatch.setattr(mock_agent_common, "display_cwd", lambda: "~/yolomux.dev8002")
    state: dict[str, str] = {}

    mock_agent_common.print_codex_startup(state)
    assert state["codex_startup_inline_composer"] == "1"

    mock_agent_common.render_inline_composer("what time is it?", len("what time is it?"), state=state)

    rendered = output.getvalue()
    prompt_index = rendered.rindex("› what time is it?")
    assert "\x1b[1;1H\x1b[2K" not in rendered
    assert rendered.index("╰") < prompt_index
    assert state["codex_startup_inline_composer"] == "1"


def test_codex_compact_startup_preserves_box_before_first_typed_prompt(monkeypatch):
    class TtyBuffer(io.StringIO):
        def isatty(self):
            return True

        def fileno(self):
            raise OSError

    class TtyInput:
        def isatty(self):
            return True

        def fileno(self):
            return 0

    output = TtyBuffer()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stdin", TtyInput())
    monkeypatch.setattr(mock_agent_common.termios, "tcgetattr", lambda _fd: ["old"])
    monkeypatch.setattr(mock_agent_common.termios, "tcsetattr", lambda _fd, _when, _settings: None)
    monkeypatch.setattr(mock_agent_common.tty, "setraw", lambda _fd: None)
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "codex")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "›")
    monkeypatch.setattr(mock_agent_common, "MODEL", "gpt-5.5")
    monkeypatch.setattr(mock_agent_common, "EFFORT", "xhigh")
    monkeypatch.setattr(mock_agent_common, "AGENT_PRODUCT_NAME", "OpenAI Codex")
    monkeypatch.setattr(mock_agent_common, "VERSION", "0.142.0")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    monkeypatch.setattr(mock_agent_common, "display_cwd", lambda: "~/yolomux.dev8002")
    state: dict[str, str] = {}
    keys = iter(["m", "o", "c", "k", "\r"])
    monkeypatch.setattr(mock_agent_common, "read_key", lambda timeout=0.12: next(keys))

    mock_agent_common.print_codex_startup(state)
    assert state["codex_startup_inline_composer"] == "1"

    user_input = mock_agent_common.read_live_composer(state)

    rendered = output.getvalue()
    assert user_input == "mock"
    prompt_index = rendered.rindex("› mock")
    assert "\x1b[1;1H\x1b[2K" not in rendered
    assert rendered.index("╰") < rendered.index("Explain this codebase") < prompt_index
    assert "codex_startup_inline_composer" not in state


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
    animated_working = mock_agent_common.codex_working_display_line(0)
    shifted_working = mock_agent_common.codex_working_display_line(mock_agent_common.CODEX_WORKING_SWEEP_FRAME_SECONDS)
    assert mock_agent_common.ANSI_RE.sub("", animated_working) == "• Working (0s • esc to interrupt)"
    assert mock_agent_common.ANSI_RE.sub("", shifted_working) == "• Working (0s • esc to interrupt)"
    assert animated_working != shifted_working
    assert animated_working.startswith("\x1b[1m•\x1b[0m")
    assert "\x1b[1mW\x1b[0m" in shifted_working

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
    plain_rendered = mock_agent_common.ANSI_RE.sub("", rendered)
    assert "\x1b[6;1H\x1b[2K" in rendered
    assert "• Working (" in plain_rendered
    assert "1 background terminal running" in plain_rendered
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
    separator = "\x1b[2m" + ("─" * 23) + "\x1b[0m"
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
    separator = "\x1b[2m" + ("─" * 79) + "\x1b[0m"
    assert "\x1b[1;8r\x1b[8;1H\x1b[2K❯ help\n" in output
    assert "tmux focus-events off" not in output
    assert f"\x1b[9;1H\x1b[2K{separator}" in output
    assert '❯ \x1b[2mTry "fix typecheck errors"\x1b[0m' in output
    assert f"\x1b[11;1H\x1b[2K{separator}" in output
    assert "? for shortcuts · ← for agents" in output
    assert output.endswith("\x1b[1;8r\x1b[8;1H")


def test_claude_first_submit_scrolls_startup_header_before_prompt(monkeypatch, capsys):
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "claude")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "❯")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    state = {
        "claude_startup_header_visible": "1",
        "claude_startup_header_top": "6",
        "claude_startup_header_bottom": "8",
    }

    mock_agent_common.finish_live_composer("mock", state)

    output = capsys.readouterr().out
    assert "\x1b[1;8r\x1b[8;1H\n\x1b[8;1H\x1b[2K❯ mock\n" in output
    assert "claude_startup_header_visible" not in state
    assert "claude_startup_header_top" not in state
    assert "claude_startup_header_bottom" not in state


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


def test_live_composer_redraws_when_terminal_size_changes(monkeypatch, capsys):
    size = {"width": 80, "height": 12}
    state = {}
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "claude")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "❯")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: size["width"])
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: size["height"])

    mock_agent_common.render_live_composer("resize me", len("resize me"), state=state)

    assert state["live_composer_terminal_width"] == "80"
    assert state["live_composer_terminal_height"] == "12"
    capsys.readouterr()

    size.update({"width": 60, "height": 10})
    assert mock_agent_common.maybe_redraw_live_composer_for_resize("resize me", len("resize me"), state=state) is True

    output = capsys.readouterr().out
    assert "\x1b7\x1b[r\x1b8" in output
    assert "\x1b[8;1H\x1b[2K❯ resize me" in output
    assert state["live_composer_terminal_width"] == "60"
    assert state["live_composer_terminal_height"] == "10"
    assert mock_agent_common.maybe_redraw_live_composer_for_resize("resize me", len("resize me"), state=state) is False


def test_claude_header_redraw_clears_previous_rows_after_resize(monkeypatch, capsys):
    state = {
        "claude_startup_header_visible": "1",
        "claude_startup_header_top": "6",
        "claude_startup_header_bottom": "8",
        "live_composer_terminal_width": "80",
        "live_composer_terminal_height": "12",
    }
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "claude")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "❯")
    monkeypatch.setattr(mock_agent_common, "MODEL_LINE", "Opus 4.8 (1M context) with xhigh effort · API Usage Billing")
    monkeypatch.setattr(mock_agent_common, "WELCOME_ORG_LINE", "· GPU Corporation - Power Users")
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 20)

    assert mock_agent_common.maybe_redraw_live_composer_for_resize("", 0, state=state) is True

    output = capsys.readouterr().out
    assert "\x1b[6;1H\x1b[2K" in output
    assert "\x1b[14;1H\x1b[2K" in output
    assert "\x1b[15;1H\x1b[2K" in output
    assert "\x1b[16;1H\x1b[2K" in output
    assert state["claude_startup_header_top"] == "14"
    assert state["claude_startup_header_bottom"] == "16"


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


def test_claude_startup_tty_places_header_above_fixed_footer(monkeypatch):
    class TtyBuffer(io.StringIO):
        def isatty(self):
            return True

    output = TtyBuffer()
    state = {}
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(mock_agent_common, "STARTUP_STYLE", "default")
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "claude")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "❯")
    monkeypatch.setattr(mock_agent_common, "VERSION", "2.1.185")
    monkeypatch.setattr(mock_agent_common, "MODEL_LINE", "Opus 4.8 (1M context) with xhigh effort · API Usage Billing")
    monkeypatch.setattr(mock_agent_common, "WELCOME_ORG_LINE", "· GPU Corporation - Power Users")
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 12)
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 80)
    monkeypatch.setattr(mock_agent_common, "launched_from_interactive_shell", lambda: False)

    mock_agent_common.print_startup(state)

    rendered = output.getvalue()
    separator = "\x1b[2m" + ("─" * 79) + "\x1b[0m"
    assert "\x1b[H\x1b[J" not in rendered
    assert "\x1b[6;1H\x1b[2K" in rendered
    assert "\x1b[7;1H\x1b[2K" in rendered
    assert "\x1b[8;1H\x1b[2K" in rendered
    assert f"\x1b[9;1H\x1b[2K{separator}" in rendered
    assert rendered.index("\x1b[8;1H\x1b[2K") < rendered.index(f"\x1b[9;1H\x1b[2K{separator}")
    assert '❯ \x1b[2mTry "fix typecheck errors"\x1b[0m' in rendered
    plain_rendered = mock_agent_common.ANSI_RE.sub("", rendered)
    assert "… · API Usage Billing · GPU Corporation - Power Users" in plain_rendered
    assert state["claude_startup_header_top"] == "6"
    assert state["claude_startup_header_bottom"] == "8"


def test_claude_startup_tty_shell_launch_flows_instead_of_clearing_history_rows(monkeypatch):
    class TtyBuffer(io.StringIO):
        def isatty(self):
            return True

    output = TtyBuffer()
    state = {}
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(mock_agent_common, "STARTUP_STYLE", "default")
    monkeypatch.setattr(mock_agent_common, "PERMISSION_STYLE", "claude")
    monkeypatch.setattr(mock_agent_common, "PROMPT_GLYPH", "❯")
    monkeypatch.setattr(mock_agent_common, "VERSION", "2.1.185")
    monkeypatch.setattr(mock_agent_common, "MODEL_LINE", "Opus 4.8 (1M context) with xhigh effort · API Usage Billing")
    monkeypatch.setattr(mock_agent_common, "terminal_height", lambda: 8)
    monkeypatch.setattr(mock_agent_common, "terminal_width", lambda: 120)
    monkeypatch.setattr(mock_agent_common, "launched_from_interactive_shell", lambda: True)

    mock_agent_common.print_startup(state)

    rendered = output.getvalue()
    assert "Claude Code v2.1.185" in rendered
    assert "▘▘ ▝▝" in rendered
    assert "\x1b[2;1H\x1b[2K" not in rendered
    assert "\x1b[3;1H\x1b[2K" not in rendered
    assert "\x1b[4;1H\x1b[2K" not in rendered
    assert "claude_startup_header_visible" not in state
    assert "claude_startup_header_top" not in state
    assert "claude_startup_header_bottom" not in state


def test_claude_startup_skips_tiny_tty_without_top_clear(monkeypatch):
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

    assert output.getvalue() == ""
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
    assert "\x1b[14;1H" in output.getvalue()
    assert "\x1b[15;1H" in output.getvalue()
    assert "\x1b[16;1H" in output.getvalue()
    assert "\x1b[1;1H" not in output.getvalue()
    assert "claude_startup_header_pending" not in state
    assert state["claude_startup_header_visible"] == "1"


@pytest.mark.e2e
@pytest.mark.socket
def test_tmux_codex_startup_box_matches_requested_shape_and_survives_first_submit(visual_tmux):
    expected_box = expected_codex_startup_box("gpt-5.5", "xhigh", "~")
    session = visual_tmux.launch(
        "codex-startup",
        [sys.executable, "tools/codex.py", "--mock", "-m", "gpt-5.5", "--effort", "xhigh", "-C", "~"],
        width=103,
        height=58,
    )

    booted, pane = visual_tmux.wait_until(session, lambda text: "OpenAI Codex (v0.142.0)" in text and "› Explain this codebase" in text)
    assert booted, pane
    assert extract_first_box(pane) == expected_box
    assert_no_startup_ellipsis(expected_box)

    visual_tmux.send_keys(session, "bogus", "Enter")
    answered, pane = visual_tmux.wait_until(session, lambda text: "I don't know how to handle \"bogus\"" in text)
    assert answered, pane
    assert expected_box == extract_first_box(visual_tmux.capture(session, scrollback=True))
    assert "› bogus" in pane


@pytest.mark.e2e
@pytest.mark.socket
def test_tmux_codex_startup_expands_long_model_row_without_ellipsis(visual_tmux):
    session = visual_tmux.launch(
        "codex-long-model",
        [sys.executable, "tools/codex.py", "--mock", "-m", "gpt-5.4-mini", "--effort", "medium", "-C", str(REPO_ROOT)],
        width=103,
        height=58,
    )

    booted, pane = visual_tmux.wait_until(session, lambda text: "gpt-5.4-mini medium" in text and "/model to change" in text)
    assert booted, pane
    box = extract_first_box(pane)
    assert box == expected_codex_startup_box("gpt-5.4-mini", "medium", expected_display_cwd(REPO_ROOT))
    assert_no_startup_ellipsis(box)
    assert len({len(line) for line in box}) == 1


@pytest.mark.e2e
@pytest.mark.socket
def test_tmux_codex_compact_startup_keeps_box_after_typing(visual_tmux):
    expected_box = expected_codex_startup_box("gpt-5.5", "xhigh", "~")
    session = visual_tmux.launch(
        "codex-compact",
        [sys.executable, "tools/codex.py", "--mock", "-m", "gpt-5.5", "--effort", "xhigh", "-C", "~"],
        width=80,
        height=12,
    )

    booted, pane = visual_tmux.wait_until(session, lambda text: "OpenAI Codex (v0.142.0)" in text and "› Explain this codebase" in text)
    assert booted, pane
    assert extract_first_box(pane) == expected_box

    visual_tmux.send_keys(session, "mock")
    typed, pane = visual_tmux.wait_until(session, lambda text: "› mock" in text)
    assert typed, pane
    assert extract_first_box(pane) == expected_box
    assert pane.index("╰─────────────────────────────────────────────╯") < pane.index("› mock")


@pytest.mark.e2e
@pytest.mark.socket
def test_tmux_claude_startup_cwd_survives_first_mock_submit(visual_tmux):
    cwd_row = f"▘▘ ▝▝    {expected_display_cwd(REPO_ROOT)}"
    session = visual_tmux.launch(
        "claude-startup-submit",
        [sys.executable, "tools/claude.py", "--mock", "-m", "opus", "--effort", "xhigh", "-C", str(REPO_ROOT)],
        width=120,
        height=42,
    )

    booted, pane = visual_tmux.wait_until(session, lambda text: cwd_row in text and '❯ Try "fix typecheck errors"' in text)
    assert booted, pane
    visual_tmux.send_keys(session, "mock", "Enter")
    listed, pane = visual_tmux.wait_until(
        session,
        lambda text: cwd_row in text and "❯ mock" in text and "working_visible_counter [RUN]" in text,
    )
    assert listed, pane
    assert pane.index(cwd_row) < pane.index("❯ mock") < pane.index("● Mock fixture cases")


@pytest.mark.socket
def test_tmux_claude_shell_launch_scrolls_history_instead_of_clobbering(visual_tmux):
    session = visual_tmux.launch(
        "claude-shell-launch",
        ["bash", "--noprofile", "--norc"],
        width=160,
        height=8,
    )

    visual_tmux.send_keys(session, "printf 'history-one\\nhistory-two\\nhistory-three\\nhistory-four\\n'", "Enter")
    printed, pane = visual_tmux.wait_until(session, lambda text: "history-four" in text)
    assert printed, pane
    visual_tmux.send_keys(session, "python3 ./utils/claude.py --mock", "Enter")
    booted, pane = visual_tmux.wait_until(
        session,
        lambda text: "Claude Code v2.1.185" in text and '❯ Try "fix typecheck errors"' in text,
    )
    assert booted, pane

    scrollback = visual_tmux.capture(session, scrollback=True)
    for line in ("history-one", "history-two", "history-three", "history-four"):
        assert line in scrollback


@pytest.mark.e2e
@pytest.mark.socket
def test_tmux_codex_working_command_counter_keeps_single_visual_working_block_while_typing(visual_tmux):
    session = visual_tmux.launch(
        "codex-working",
        [sys.executable, "tools/codex.py", "--mock", "-m", "gpt-5.5", "--effort", "xhigh", "-C", "~"],
        width=103,
        height=30,
    )

    booted, pane = visual_tmux.wait_until(session, lambda text: "› Explain this codebase" in text)
    assert booted, pane
    visual_tmux.send_keys(session, "mock working_command_counter", "Enter")
    working, pane = visual_tmux.wait_until(session, lambda text: "• Working (" in text and "esc to interrupt" in text)
    assert working, pane
    assert pane.count("• Working (") == 1

    visual_tmux.send_keys(session, "queued follow-up")
    queued, pane = visual_tmux.wait_until(session, lambda text: "› queued follow-up" in text and "• Working (" in text)
    assert queued, pane
    assert pane.count("• Working (") == 1
    assert "esc to interrupt" in pane


@pytest.mark.e2e
@pytest.mark.socket
def test_tmux_mock_fixture_list_wraps_rows_instead_of_printing_ellipsis_after_resize(visual_tmux):
    long_fixture_file = "question_with_answer_draft__codex-cli-0.141.0_20260620.yaml"
    session = visual_tmux.launch(
        "codex-mock-list-resize",
        [sys.executable, "tools/codex.py", "--mock", "-m", "gpt-5.4-mini", "--effort", "medium", "-C", str(REPO_ROOT)],
        width=92,
        height=42,
    )

    booted, pane = visual_tmux.wait_until(session, lambda text: "› Explain this codebase" in text)
    assert booted, pane
    visual_tmux.send_keys(session, "mock", "Enter")
    listed, pane = visual_tmux.wait_until(session, lambda text: "Mock fixture cases" in text and "question_with_answer_draft" in text)
    assert listed, pane
    assert "…" not in pane
    assert "..." not in pane
    assert long_fixture_file in visual_tmux.capture(session, scrollback=True, join_wrapped=True)

    visual_tmux.resize(session, width=150, height=42)
    resized = visual_tmux.capture(session, scrollback=True, join_wrapped=True)
    resized_rows = "\n".join(line for line in resized.splitlines() if "⎿" in line)
    assert "…" not in resized_rows
    assert "..." not in resized_rows
    assert long_fixture_file in resized


@pytest.mark.e2e
@pytest.mark.socket
def test_tmux_claude_mock_fixture_list_wraps_rows_instead_of_printing_ellipsis_after_resize(visual_tmux):
    long_fixture_file = "parser_dependency_choice_question__claude-code-2.1.185_20260622.yaml"
    session = visual_tmux.launch(
        "claude-mock-list-resize",
        [sys.executable, "tools/claude.py", "--mock", "-m", "sonnet", "--effort", "medium", "-C", str(REPO_ROOT)],
        width=92,
        height=42,
    )

    booted, pane = visual_tmux.wait_until(session, lambda text: '❯ Try "fix typecheck errors"' in text)
    assert booted, pane
    visual_tmux.send_keys(session, "mock", "Enter")
    listed, pane = visual_tmux.wait_until(session, lambda text: "parser_dependency_choice_question [ASK?]" in text)
    assert listed, pane
    list_rows = "\n".join(line for line in pane.splitlines() if "⎿" in line)
    assert "…" not in list_rows
    assert "..." not in list_rows
    assert long_fixture_file in visual_tmux.capture(session, scrollback=True, join_wrapped=True)

    visual_tmux.resize(session, width=150, height=42)
    resized = visual_tmux.capture(session, scrollback=True, join_wrapped=True)
    resized_rows = "\n".join(line for line in resized.splitlines() if "⎿" in line)
    assert "…" not in resized_rows
    assert "..." not in resized_rows
    assert long_fixture_file in resized


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
