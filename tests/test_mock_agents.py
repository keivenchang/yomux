import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

import pytest
import yaml

from yolomux_lib.agent_tui import classify_agent_pane
from yolomux_lib.tmux_utils import YOLOMUX_TMUX_SOCKET_ENV


REPO_ROOT = Path(__file__).resolve().parents[1]
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
