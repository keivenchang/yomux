#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Verify YOLOmux agent_tui against a real Claude/Codex tmux pane."""

from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from yolomux_lib.agent_tui import AgentTuiCursor
from yolomux_lib.agent_tui import agent_client_version_slug
from yolomux_lib.agent_tui import classify_agent_pane
from yolomux_lib.agent_tui import clear_composer
from yolomux_lib.agent_tui import send_prompt
from yolomux_lib.agent_tui import wait_until_accepting


TOKEN_RE = re.compile(
    r"\b(?:"
    r"sk-[A-Za-z0-9_-]{10,}"
    r"|ghp_[A-Za-z0-9_]{10,}"
    r"|github_pat_[A-Za-z0-9_]{10,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|lin_api_[A-Za-z0-9_]{10,}"
    r"|hf_[A-Za-z0-9_]{10,}"
    r")\b"
)


class LiteralStringDumper(yaml.SafeDumper):
    pass


def _represent_string(dumper: yaml.SafeDumper, value: str) -> yaml.nodes.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


LiteralStringDumper.add_representer(str, _represent_string)


def dump_fixture_yaml(data: dict[str, Any]) -> str:
    return yaml.dump(data, Dumper=LiteralStringDumper, allow_unicode=True, sort_keys=True, width=120)


def sanitize_text(text: str) -> str:
    home = os.path.expanduser("~")
    sanitized = str(text or "")
    if home:
        sanitized = sanitized.replace(home, "~")
    sanitized = TOKEN_RE.sub("<redacted-token>", sanitized)
    sanitized = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "<redacted-email>", sanitized)
    return sanitized.rstrip() + "\n"


def tmux_base(socket: str) -> list[str]:
    executable = shutil.which("tmux")
    if not executable:
        raise SystemExit("tmux is not installed")
    command = [executable]
    if socket:
        command.extend(["-S", socket])
    return command


def run_tmux(socket: str, args: list[str], timeout: float = 10.0, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*tmux_base(socket), *args], input=input_text, capture_output=True, text=True, timeout=timeout, check=False)


def tmux_capture(socket: str, target: str, *, styled: bool = False, visible_only: bool = True) -> str | None:
    command = ["capture-pane"]
    if styled:
        command.append("-e")
    command.extend(["-p", "-J", "-t", target])
    if not visible_only:
        command.extend(["-S", "-2000"])
    result = run_tmux(socket, command)
    if result.returncode != 0:
        return None
    return result.stdout


def cursor_state(socket: str, target: str) -> AgentTuiCursor:
    result = run_tmux(socket, ["display-message", "-p", "-t", target, "#{cursor_x}\t#{cursor_y}\t#{cursor_character}\t#{pane_in_mode}\t#{pane_current_command}"])
    if result.returncode != 0:
        return AgentTuiCursor(error=(result.stderr or result.stdout or "tmux display-message failed").strip())
    parts = result.stdout.rstrip("\n").split("\t")
    while len(parts) < 5:
        parts.append("")
    try:
        x = int(parts[0])
        y = int(parts[1])
    except ValueError:
        return AgentTuiCursor(character=parts[2], pane_in_mode=parts[3] == "1", current_command=parts[4], error="tmux cursor output was not numeric")
    return AgentTuiCursor(x=x, y=y, character=parts[2], pane_in_mode=parts[3] == "1", current_command=parts[4])


def tmux_clear_input(socket: str, target: str) -> subprocess.CompletedProcess[str]:
    return run_tmux(socket, ["send-keys", "-t", target, "C-e", "C-u"])


def tmux_paste_text(socket: str, target: str, text: str, *, submit: bool = False) -> subprocess.CompletedProcess[str]:
    buffer_name = f"agent-tui-verify-{secrets.token_hex(8)}"
    load = run_tmux(socket, ["load-buffer", "-b", buffer_name, "-"], input_text=str(text or ""))
    if load.returncode != 0:
        return load
    try:
        paste = run_tmux(socket, ["paste-buffer", "-p", "-t", target, "-b", buffer_name])
        if paste.returncode != 0 or not submit:
            return paste
        enter = run_tmux(socket, ["send-keys", "-t", target, "Enter"])
        return enter if enter.returncode != 0 else paste
    finally:
        run_tmux(socket, ["delete-buffer", "-b", buffer_name], timeout=1.0)


def command_version(command: str) -> str:
    executable = shutil.which(command)
    if not executable:
        return ""
    result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=8, check=False)
    lines = (result.stdout or result.stderr or "").strip().splitlines()
    return sanitize_text(lines[0]).strip() if lines else ""


def agent_command(agent: str) -> str:
    if agent == "claude":
        return "claude"
    if agent == "codex":
        return "codex"
    raise SystemExit("--launch requires --agent claude or --agent codex")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=["claude", "codex", "unknown"], default="unknown")
    parser.add_argument("--target", help="existing tmux target, for example 8001:0.0 or %%pane_id")
    parser.add_argument("--launch", action="store_true", help="launch the selected agent in a temporary tmux session")
    parser.add_argument("--launch-command", default="", help="exact command to launch instead of the default agent command")
    parser.add_argument("--session", default="", help="tmux session name when launching")
    parser.add_argument("--socket", default="", help="tmux socket path")
    parser.add_argument("--send-line", action="append", default=[], help="line to send followed by Enter before verification")
    parser.add_argument("--type-text", action="append", default=[], help="text to paste into the composer without pressing Enter before verification")
    parser.add_argument("--wait-until-accepting", action="store_true", help="poll the central classifier until the pane accepts input before other actions")
    parser.add_argument("--accepting-timeout", type=float, default=15.0, help="seconds to wait when --wait-until-accepting is used")
    parser.add_argument("--text-timeout", type=float, default=15.0, help="seconds to wait for --ready-text or --wait-text")
    parser.add_argument("--poll-seconds", type=float, default=0.25, help="poll interval for wait/clear/send verification")
    parser.add_argument("--ready-text", action="append", default=[], help="visible text that must appear before send/actions; repeatable")
    parser.add_argument("--wait-text", action="append", default=[], help="visible text that must appear after --send-line; repeatable")
    parser.add_argument("--require-visible-capture", action="store_true", help="fail if the final raw/styled visible capture is blank")
    parser.add_argument("--clear-composer", action="store_true", help="call agent_tui.clear_composer() against the target before final verification")
    parser.add_argument("--send-prompt", action="append", default=[], help="call agent_tui.send_prompt() with this prompt; repeatable")
    parser.add_argument("--no-submit", action="store_true", help="paste --send-prompt text without pressing Enter")
    parser.add_argument("--no-clear-existing", action="store_true", help="do not clear existing composer text before --send-prompt")
    parser.add_argument("--no-verify-submit", action="store_true", help="do not verify that submitted text left the composer after --send-prompt")
    parser.add_argument("--wait-seconds", type=float, default=1.0, help="seconds to wait after launch/send before capture")
    parser.add_argument("--expected-screen-key", choices=["idle", "working", "approval", "needs-input", "input-draft", "disconnected", "error"])
    parser.add_argument("--expected-reason-code", default="")
    parser.add_argument("--expected-attention-kind", choices=["", "working", "approval", "question"], default="")
    parser.add_argument("--expected-approval-visible", choices=["true", "false"], default="")
    parser.add_argument("--expected-agent-kind", choices=["", "claude", "codex"], default="")
    parser.add_argument("--expected-composer-key", choices=["", "empty", "ghost", "draft", "unknown"], default="")
    parser.add_argument("--expected-clear-ok", choices=["true", "false"], default="")
    parser.add_argument("--expected-clear-cleared", choices=["true", "false"], default="")
    parser.add_argument("--expected-send-ok", choices=["true", "false"], default="")
    parser.add_argument("--expected-send-reason-code", default="")
    parser.add_argument("--staging-dir", type=Path, default=Path("tests/fixtures/prompt_corpus/staging"))
    parser.add_argument("--write-capture", action="store_true", help="write capture metadata even when checks pass")
    parser.add_argument("--kill-launched", action="store_true", help="kill launched tmux session at exit")
    return parser.parse_args()


def launch_target(args: argparse.Namespace) -> tuple[str, str]:
    if not args.launch:
        if not args.target:
            raise SystemExit("--target is required unless --launch is used")
        return args.target, ""
    session = args.session or f"agent-tui-verify-{args.agent}-{int(time.time())}"
    command = args.launch_command or agent_command(args.agent)
    result = run_tmux(args.socket, ["new-session", "-d", "-s", session, command])
    if result.returncode != 0:
        raise SystemExit((result.stderr or result.stdout or "tmux new-session failed").strip())
    return f"{session}:", session


def send_lines(args: argparse.Namespace, target: str) -> None:
    for line in args.send_line:
        result = run_tmux(args.socket, ["send-keys", "-t", target, line, "Enter"])
        if result.returncode != 0:
            raise SystemExit((result.stderr or result.stdout or "tmux send-keys failed").strip())


def wait_for_visible_text(args: argparse.Namespace, target: str, needles: list[str], label: str) -> dict[str, Any]:
    if not needles:
        return {"type": label, "ok": True, "needles": []}
    deadline = time.monotonic() + max(0.0, args.text_timeout)
    last_raw = ""
    last_styled = ""
    while True:
        last_raw = tmux_capture(args.socket, target, styled=False, visible_only=True) or ""
        last_styled = tmux_capture(args.socket, target, styled=True, visible_only=True) or ""
        haystack = f"{last_raw}\n{last_styled}"
        if all(needle in haystack for needle in needles):
            return {"type": label, "ok": True, "needles": list(needles)}
        if time.monotonic() >= deadline:
            return {
                "type": label,
                "ok": False,
                "needles": list(needles),
                "last_raw_tail": sanitize_text(last_raw[-1000:]),
                "last_styled_tail": sanitize_text(last_styled[-1000:]),
            }
        time.sleep(max(0.01, args.poll_seconds))


def capture_funcs(args: argparse.Namespace):
    def capture_func(pane_target: str, visible_only: bool = True) -> str | None:
        return tmux_capture(args.socket, pane_target, styled=False, visible_only=visible_only)

    def capture_styled_func(pane_target: str, visible_only: bool = True) -> str | None:
        return tmux_capture(args.socket, pane_target, styled=True, visible_only=visible_only)

    def cursor_func(pane_target: str) -> AgentTuiCursor:
        return cursor_state(args.socket, pane_target)

    return capture_func, capture_styled_func, cursor_func


def classify_target(args: argparse.Namespace, target: str) -> tuple[dict[str, Any], str, str]:
    capture_func, capture_styled_func, cursor_func = capture_funcs(args)
    raw_capture = tmux_capture(args.socket, target, styled=False, visible_only=True) or ""
    styled_capture = tmux_capture(args.socket, target, styled=True, visible_only=True) or ""
    state = classify_agent_pane(
        {"pane_target": target, "agent_kind": "" if args.agent == "unknown" else args.agent},
        session=target.split(":", 1)[0],
        prompt_source="pane",
        include_composer=True,
        include_cursor=True,
        include_transcript_activity=False,
        capture_func=capture_func,
        capture_styled_func=capture_styled_func,
        cursor_func=cursor_func,
    )
    return state.as_dict(), raw_capture, styled_capture


def run_central_actions(args: argparse.Namespace, target: str) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    capture_func, capture_styled_func, _cursor_func = capture_funcs(args)

    if args.wait_until_accepting:
        def classify_func(_candidate: str | dict[str, Any]):
            state = classify_agent_pane(
                {"pane_target": target, "agent_kind": "" if args.agent == "unknown" else args.agent},
                session=target.split(":", 1)[0],
                prompt_source="pane",
                include_composer=True,
                include_cursor=True,
                include_transcript_activity=False,
                capture_func=capture_func,
                capture_styled_func=capture_styled_func,
                cursor_func=lambda pane_target: cursor_state(args.socket, pane_target),
            )
            return state

        state = wait_until_accepting(
            {"pane_target": target, "agent_kind": "" if args.agent == "unknown" else args.agent},
            timeout=args.accepting_timeout,
            poll=args.poll_seconds,
            classify_func=classify_func,
        )
        operations.append({"type": "wait_until_accepting", "state": state.as_dict()})

    for text in args.type_text:
        result = tmux_paste_text(args.socket, target, text, submit=False)
        operations.append({
            "type": "type_text",
            "text_length": len(text),
            "returncode": result.returncode,
            "error": sanitize_text(result.stderr or result.stdout).strip() if result.returncode != 0 else "",
        })

    if args.clear_composer:
        result = clear_composer(
            target,
            wait_seconds=args.wait_seconds,
            poll_seconds=args.poll_seconds,
            capture_func=capture_func,
            capture_styled_func=capture_styled_func,
            cursor_func=lambda pane_target: cursor_state(args.socket, pane_target),
            clear_func=lambda pane_target: tmux_clear_input(args.socket, pane_target),
        )
        operations.append({"type": "clear_composer", "result": result.as_dict()})

    for prompt in args.send_prompt:
        result = send_prompt(
            {"pane_target": target, "agent_kind": "" if args.agent == "unknown" else args.agent},
            prompt,
            submit=not args.no_submit,
            clear_existing=not args.no_clear_existing,
            verify_submit=not args.no_verify_submit,
            clear_wait_seconds=args.wait_seconds,
            clear_poll_seconds=args.poll_seconds,
            verify_wait_seconds=args.wait_seconds,
            verify_poll_seconds=args.poll_seconds,
            paste_func=lambda pane_target, text, submit=False: tmux_paste_text(args.socket, pane_target, text, submit=submit),
            capture_func=capture_func,
            capture_styled_func=capture_styled_func,
            cursor_func=lambda pane_target: cursor_state(args.socket, pane_target),
            clear_func=lambda pane_target: tmux_clear_input(args.socket, pane_target),
        )
        operations.append({"type": "send_prompt", "text_length": len(prompt), "result": result.as_dict()})

    return operations


def _expected_bool(value: str) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def _last_operation(operations: list[dict[str, Any]], operation_type: str) -> dict[str, Any]:
    for operation in reversed(operations):
        if operation.get("type") == operation_type:
            return operation
    return {}


def expected_failures(args: argparse.Namespace, state: dict[str, Any], operations: list[dict[str, Any]], raw_capture: str, styled_capture: str) -> list[str]:
    failures = []
    screen = state.get("screen") if isinstance(state.get("screen"), dict) else {}
    approval = state.get("approval") if isinstance(state.get("approval"), dict) else {}
    composer = state.get("composer") if isinstance(state.get("composer"), dict) else {}
    if args.require_visible_capture and not (raw_capture.strip() or styled_capture.strip()):
        failures.append("visible capture expected nonblank raw/styled text, got blank capture")
    for operation in operations:
        if operation.get("type") in {"ready_text", "wait_text"} and operation.get("ok") is False:
            failures.append(f"{operation.get('type')} did not appear: {operation.get('needles')!r}")
    if args.expected_screen_key and screen.get("key") != args.expected_screen_key:
        failures.append(f"screen.key expected {args.expected_screen_key!r}, got {screen.get('key')!r}")
    if args.expected_reason_code and state.get("reason_code") != args.expected_reason_code:
        failures.append(f"reason_code expected {args.expected_reason_code!r}, got {state.get('reason_code')!r}")
    if args.expected_attention_kind and state.get("attention_kind") != args.expected_attention_kind:
        failures.append(f"attention_kind expected {args.expected_attention_kind!r}, got {state.get('attention_kind')!r}")
    if args.expected_agent_kind and state.get("agent_kind") != args.expected_agent_kind:
        failures.append(f"agent_kind expected {args.expected_agent_kind!r}, got {state.get('agent_kind')!r}")
    if args.expected_composer_key and composer.get("key") != args.expected_composer_key:
        failures.append(f"composer.key expected {args.expected_composer_key!r}, got {composer.get('key')!r}")
    if args.expected_approval_visible:
        expected = args.expected_approval_visible == "true"
        if bool(approval.get("approval_visible")) is not expected:
            failures.append(f"approval_visible expected {expected!r}, got {approval.get('approval_visible')!r}")
    clear_expected = _expected_bool(args.expected_clear_ok)
    if clear_expected is not None:
        clear_operation = _last_operation(operations, "clear_composer")
        clear_result = clear_operation.get("result") if isinstance(clear_operation.get("result"), dict) else {}
        if not clear_result:
            failures.append("clear_composer result expected, got no clear operation")
        elif bool(clear_result.get("ok")) is not clear_expected:
            failures.append(f"clear.ok expected {clear_expected!r}, got {clear_result.get('ok')!r}")
    cleared_expected = _expected_bool(args.expected_clear_cleared)
    if cleared_expected is not None:
        clear_operation = _last_operation(operations, "clear_composer")
        clear_result = clear_operation.get("result") if isinstance(clear_operation.get("result"), dict) else {}
        if not clear_result:
            failures.append("clear_composer result expected, got no clear operation")
        elif bool(clear_result.get("cleared")) is not cleared_expected:
            failures.append(f"clear.cleared expected {cleared_expected!r}, got {clear_result.get('cleared')!r}")
    send_expected = _expected_bool(args.expected_send_ok)
    if send_expected is not None:
        send_operation = _last_operation(operations, "send_prompt")
        send_result = send_operation.get("result") if isinstance(send_operation.get("result"), dict) else {}
        if not send_result:
            failures.append("send_prompt result expected, got no send operation")
        elif bool(send_result.get("ok")) is not send_expected:
            failures.append(f"send.ok expected {send_expected!r}, got {send_result.get('ok')!r}")
    if args.expected_send_reason_code:
        send_operation = _last_operation(operations, "send_prompt")
        send_result = send_operation.get("result") if isinstance(send_operation.get("result"), dict) else {}
        if not send_result:
            failures.append("send_prompt result expected, got no send operation")
        elif send_result.get("reason_code") != args.expected_send_reason_code:
            failures.append(f"send.reason_code expected {args.expected_send_reason_code!r}, got {send_result.get('reason_code')!r}")
    return failures


def capture_metadata(args: argparse.Namespace, target: str, state: dict[str, Any], raw_capture: str, styled_capture: str, operations: list[dict[str, Any]], failures: list[str]) -> dict[str, Any]:
    captured_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    claude_version = command_version("claude") if args.agent in {"claude", "unknown"} else ""
    codex_version = command_version("codex") if args.agent in {"codex", "unknown"} else ""
    client_name, client_version = capture_client_identity(args.agent, claude_version, codex_version)
    return {
        "captured_at": captured_at,
        "capture_date": captured_at[:10],
        "agent": args.agent,
        "client_name": client_name,
        "client_version": client_version,
        "client_version_slug": client_version_slug(args.agent, client_version),
        "claude_version": claude_version,
        "codex_version": codex_version,
        "verifier_capabilities": [
            "raw-capture",
            "styled-capture",
            "cursor-facts",
            "central-clear-composer",
            "central-send-prompt",
            "visible-status-counter",
        ],
        "tmux_target": target,
        "tmux_socket": args.socket,
        "capture_mode": "visible",
        "raw_capture": sanitize_text(raw_capture),
        "styled_capture": sanitize_text(styled_capture),
        "cursor": state.get("cursor") or {},
        "operations": operations,
        "expected": {
            "screen_key": args.expected_screen_key or "",
            "reason_code": args.expected_reason_code,
            "attention_kind": args.expected_attention_kind,
            "approval_visible": args.expected_approval_visible,
            "agent_kind": args.expected_agent_kind,
            "composer_key": args.expected_composer_key,
            "clear_ok": args.expected_clear_ok,
            "clear_cleared": args.expected_clear_cleared,
            "send_ok": args.expected_send_ok,
            "send_reason_code": args.expected_send_reason_code,
        },
        "actual": {
            "screen": state.get("screen") or {},
            "display": state.get("display") or {},
            "approval": state.get("approval") or {},
            "composer": state.get("composer") or {},
            "cursor": state.get("cursor") or {},
            "attention_kind": state.get("attention_kind") or "",
            "attention_label": state.get("attention_label") or "",
            "reason_code": state.get("reason_code") or "",
            "agent_kind": state.get("agent_kind") or "",
        },
        "evidence_fields": {
            "screen_evidence_lines": (state.get("screen") or {}).get("evidence_lines") if isinstance(state.get("screen"), dict) else [],
            "display_evidence_lines": (state.get("display") or {}).get("evidence_lines") if isinstance(state.get("display"), dict) else [],
            "prompt_hash": (state.get("display") or {}).get("prompt_hash") if isinstance(state.get("display"), dict) else "",
        },
        "failures": failures,
    }


def capture_client_identity(agent: str, claude_version: str, codex_version: str) -> tuple[str, str]:
    if agent == "claude":
        return "Claude Code", claude_version
    if agent == "codex":
        return "Codex CLI", codex_version
    if claude_version:
        return "Claude Code", claude_version
    if codex_version:
        return "Codex CLI", codex_version
    return "unknown", ""


def client_version_slug(agent: str, version: str) -> str:
    return agent_client_version_slug(agent, version)


def write_capture(staging_dir: Path, agent: str, metadata: dict[str, Any]) -> Path:
    staging_dir.mkdir(parents=True, exist_ok=True)
    captured_at = str(metadata.get("captured_at") or "")
    try:
        stamp = datetime.datetime.fromisoformat(captured_at).strftime("%Y%m%dT%H%M%SZ")
    except ValueError:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    version_slug = str(metadata.get("client_version_slug") or client_version_slug(agent, str(metadata.get("client_version") or "")))
    path = staging_dir / f"agent_tui__{version_slug}_{stamp}.yaml"
    path.write_text(dump_fixture_yaml(metadata), encoding="utf-8")
    return path


def main() -> int:
    args = parse_args()
    target, launched_session = launch_target(args)
    try:
        if args.wait_seconds > 0:
            time.sleep(args.wait_seconds)
        operations: list[dict[str, Any]] = []
        operations.append(wait_for_visible_text(args, target, args.ready_text, "ready_text"))
        send_lines(args, target)
        operations.append(wait_for_visible_text(args, target, args.wait_text, "wait_text"))
        if args.send_line and args.wait_seconds > 0:
            time.sleep(args.wait_seconds)
        operations.extend(run_central_actions(args, target))
        if any(operation.get("type") not in {"ready_text", "wait_text"} for operation in operations) and args.wait_seconds > 0:
            time.sleep(args.wait_seconds)
        state, raw_capture, styled_capture = classify_target(args, target)
        failures = expected_failures(args, state, operations, raw_capture, styled_capture)
        metadata = capture_metadata(args, target, state, raw_capture, styled_capture, operations, failures)
        if failures or args.write_capture:
            path = write_capture(args.staging_dir, args.agent, metadata)
            print(f"capture: {path}")
        summary = {
            "agent": metadata["agent"],
            "claude_version": metadata["claude_version"],
            "codex_version": metadata["codex_version"],
            "tmux_target": metadata["tmux_target"],
            "operations": metadata["operations"],
            **metadata["actual"],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        if failures:
            for failure in failures:
                print(f"FAIL: {failure}", file=sys.stderr)
            return 1
        return 0
    finally:
        if launched_session and args.kill_launched:
            run_tmux(args.socket, ["kill-session", "-t", launched_session])


if __name__ == "__main__":
    raise SystemExit(main())
