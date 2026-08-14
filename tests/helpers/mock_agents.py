"""Mock-agent corpus and tmux helpers shared by status and mock-agent tests."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from tests.helpers.prompt_corpus import PromptCorpus
from tests.helpers.prompt_corpus import PromptCorpusPreset

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT_CORPUS_DIR = REPO_ROOT / "tests" / "fixtures" / "prompt_corpus"
PROMPT_CORPUS = PromptCorpus(PROMPT_CORPUS_DIR, PromptCorpusPreset.MOCK_AGENTS)


def root_inventory_cases():
    return PROMPT_CORPUS.cases()


def case_command_name(case):
    data = case["data"]
    inventory = case["inventory"]
    name = str(
        data.get("case_name")
        or inventory.get("case_name")
        or inventory.get("scenario")
        or case["path"].stem
    )
    agent = str(data.get("agent") or inventory.get("expected", {}).get("agent") or "")
    return f"{agent}_{name}" if agent in {"claude", "codex"} else name


def tmux_cmd(tmux_binary, socket_path, *args, timeout=8):
    return subprocess.run(
        [tmux_binary, "-S", str(socket_path), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def short_tmux_socket_path(prefix):
    return Path(tempfile.mkdtemp(prefix=f"{prefix}-{os.getpid()}-", dir="/tmp")) / "s"


def _capture(tmux_binary, socket_path, session):
    return tmux_cmd(tmux_binary, socket_path, "capture-pane", "-p", "-t", f"{session}:").stdout or ""


def _visible_needles(text):
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
    needles = _visible_needles(expected_text)
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        last = _capture(tmux_binary, socket_path, session)
        if needles:
            if any(needle in last for needle in needles):
                return True, last
        elif all(prompt not in last for prompt in ("Implement {feature}", "Write tests for @filename", "Explain this codebase", 'Try "fix typecheck errors"')):
            return True, last
        time.sleep(0.2)
    return False, last
