# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Rule engine for YOLO auto-approval decisions."""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .common import CONFIG_DIR
from .settings import settings_payload


YOLO_RULES_PATH = CONFIG_DIR / "yolo-rules.yaml"
YOLO_RULES_DISPLAY_PATH = "~/.config/yolomux/yolo-rules.yaml"

RULE_ACTIONS = {"approve", "decline", "block", "ask", "notify", "off"}
ACTIVE_RULE_ACTIONS = {"approve", "decline"}
PASSIVE_RULE_ACTIONS = {"block", "ask", "notify", "off"}
RULE_MATCH_TYPES = {"command", "regex", "glob", "contains"}

DEFAULT_DANGEROUS_COMMANDS = (
    "dd",
    "fdisk",
    "format",
    "mkfs",
    "parted",
    "rm",
    "rmdir",
    "shred",
    "wipefs",
)
DEFAULT_DANGEROUS_REGEXES = (
    r">\s*/dev/sd",
    r">\s*/dev/nvme",
    r">\s*/dev/vd",
    r"(?:^|[;&|])\s*find\b[^|;&]*\s-delete\b",
)
BLOCK_DEVICE_RE = re.compile(r"/dev/(?:sd[a-z]\d*|nvme\d+n\d+(?:p\d+)?|vd[a-z]\d*)")
BLOCK_DEVICE_REDIRECT_RE = re.compile(r"(?:^|[\s;&|])(?:\d?>|>>)\s*/dev/(?:sd[a-z]|nvme\d|vd[a-z])")
FORK_BOMB_RE = re.compile(r":\(\)\s*\{.*:\|:&\s*\}\s*;\s*:")
COMMAND_BOUNDARIES = {";", "&&", "||", "|", "&"}
SHELL_COMMANDS = {"bash", "sh", "zsh", "dash", "fish"}
WRAPPER_COMMANDS = {"sudo", "doas", "command", "builtin", "time", "nohup", "nice"}


@dataclass(frozen=True)
class CommandInvocation:
    command: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class YoloRule:
    name: str
    match_type: str
    patterns: tuple[str, ...]
    action: str
    risk: str


@dataclass(frozen=True)
class YoloRuleset:
    default_action: str
    rules: tuple[YoloRule, ...]
    source: str
    path: Path
    builtin: bool = False


_RULES_LOCK = threading.RLock()
_RULES_CACHE: dict[str, Any] = {
    "path": None,
    "mtime_ns": None,
    "ruleset": None,
    "error": "",
}
_LAST_ERROR_PRINTED = ""


def expand_rule_path(path: str | None = None) -> Path:
    raw = path or YOLO_RULES_DISPLAY_PATH
    return Path(os.path.expandvars(os.path.expanduser(raw))).resolve()


def yolo_settings() -> dict[str, Any]:
    payload = settings_payload()
    yolo = payload.get("settings", {}).get("yolo", {})
    if not isinstance(yolo, dict):
        yolo = {}
    return {
        "rule_file_path": yolo.get("rule_file_path", YOLO_RULES_DISPLAY_PATH),
        "dry_run": yolo.get("dry_run") is True,
    }


def active_rule_path() -> Path:
    settings = yolo_settings()
    return expand_rule_path(str(settings["rule_file_path"]))


def default_rule_data(default_action: str = "approve") -> dict[str, Any]:
    return {
        "default": default_action if default_action in RULE_ACTIONS else "approve",
        "rules": [
            {
                "name": "built-in dangerous commands",
                "type": "command",
                "match": list(DEFAULT_DANGEROUS_COMMANDS),
                "action": "block",
                "risk": "delete",
            },
            {
                "name": "built-in dangerous patterns",
                "type": "regex",
                "match": list(DEFAULT_DANGEROUS_REGEXES),
                "action": "block",
                "risk": "delete",
            },
        ],
    }


def default_rule_file_text(default_action: str = "ask") -> str:
    data = default_rule_data(default_action)
    body = yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    return (
        "# YOLOmux auto-approval rules.\n"
        "# Rules are evaluated top to bottom; the first match wins.\n"
        "# Actions: approve, decline, block, ask, notify, off.\n"
        f"# Path: {YOLO_RULES_DISPLAY_PATH}\n\n"
        + body
    )


def ensure_rule_file(path: Path | None = None) -> Path:
    rule_path = path or active_rule_path()
    if rule_path.exists():
        return rule_path
    rule_path.parent.mkdir(parents=True, exist_ok=True)
    rule_path.parent.chmod(0o700)
    tmp = rule_path.with_name(f".{rule_path.name}.{os.getpid()}.{int(time.time() * 1000)}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(default_rule_file_text("ask"))
    os.replace(tmp, rule_path)
    rule_path.chmod(0o600)
    return rule_path


def shell_tokens(cmd_line: str) -> list[str]:
    try:
        lexer = shlex.shlex(cmd_line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        # Tmux captures visual lines, so long quoted Codex commands can arrive
        # temporarily incomplete. Keep rule evaluation conservative but alive.
        return re.findall(r"&&|\|\||[;&|()]|[^\s;&|()]+", cmd_line) if cmd_line.strip() else []


def is_assignment(token: str) -> bool:
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token or ""))


def normalize_command(token: str) -> str:
    cleaned = token.strip().strip("`'\"()")
    if cleaned.startswith("\\"):
        cleaned = cleaned[1:]
    if not cleaned:
        return ""
    return Path(cleaned).name


def split_segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in COMMAND_BOUNDARIES:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def skip_command_prefix(segment: list[str], start: int = 0) -> int:
    index = start
    while index < len(segment):
        token = segment[index]
        command = normalize_command(token)
        if token in {"$", "(", ")"} or is_assignment(token):
            index += 1
            continue
        if command in WRAPPER_COMMANDS:
            index += 1
            while index < len(segment) and segment[index].startswith("-"):
                index += 1
            continue
        if command == "env":
            index += 1
            while index < len(segment) and (segment[index].startswith("-") or is_assignment(segment[index])):
                index += 1
            continue
        return index
    return index


def shell_join(tokens: list[str]) -> str:
    return shlex.join([token for token in tokens if token not in {"$", "(", ")"}])


def shell_c_payload(command: str, args: tuple[str, ...]) -> str | None:
    if command not in SHELL_COMMANDS:
        return None
    for index, arg in enumerate(args):
        if arg in {"-c", "-lc"} and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith("-") and "c" in arg[1:] and index + 1 < len(args):
            return args[index + 1]
    return None


def docker_payload(command: str, args: tuple[str, ...]) -> str | None:
    if command not in {"docker", "podman"} or not args:
        return None
    subcommand = args[0]
    if subcommand not in {"exec", "run"}:
        return None
    index = 1
    while index < len(args) and args[index].startswith("-"):
        index += 1
    if subcommand in {"exec", "run"} and index < len(args):
        index += 1
    if index < len(args):
        return shell_join(list(args[index:]))
    return None


def kubectl_payload(command: str, args: tuple[str, ...]) -> str | None:
    if command != "kubectl" or not args or args[0] != "exec":
        return None
    if "--" in args:
        index = args.index("--") + 1
    else:
        index = 2
    if index >= len(args):
        return None
    return args[index] if len(args) - index == 1 else shell_join(list(args[index:]))


def ssh_payload(command: str, args: tuple[str, ...]) -> str | None:
    if command != "ssh":
        return None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            index += 1
            break
        if arg.startswith("-"):
            index += 2 if arg in {"-i", "-p", "-l", "-o"} and index + 1 < len(args) else 1
            continue
        index += 1
        break
    if index >= len(args):
        return None
    return args[index] if len(args) - index == 1 else shell_join(list(args[index:]))


def xargs_payload(command: str, args: tuple[str, ...]) -> str | None:
    if command != "xargs":
        return None
    index = 0
    while index < len(args) and args[index].startswith("-"):
        index += 1
    return shell_join(list(args[index:])) if index < len(args) else None


def find_exec_payload(command: str, args: tuple[str, ...]) -> str | None:
    if command != "find" or "-exec" not in args:
        return None
    index = args.index("-exec") + 1
    end = args.index(";", index) if ";" in args[index:] else len(args)
    return shell_join(list(args[index:end])) if index < end else None


def nested_payloads(invocation: CommandInvocation) -> list[str]:
    payloads = [
        shell_c_payload(invocation.command, invocation.args),
        docker_payload(invocation.command, invocation.args),
        kubectl_payload(invocation.command, invocation.args),
        ssh_payload(invocation.command, invocation.args),
        xargs_payload(invocation.command, invocation.args),
        find_exec_payload(invocation.command, invocation.args),
    ]
    return [payload for payload in payloads if payload]


def command_substitution_payloads(cmd_line: str) -> list[str]:
    payloads: list[str] = []
    for match in re.finditer(r"\$\(([^()]*)\)", cmd_line):
        payloads.append(match.group(1))
    for match in re.finditer(r"`([^`]+)`", cmd_line):
        payloads.append(match.group(1))
    return payloads


def command_invocations(cmd_line: str, depth: int = 0) -> list[CommandInvocation]:
    if depth > 4:
        return []
    invocations: list[CommandInvocation] = []
    tokens = shell_tokens(cmd_line)
    for segment in split_segments(tokens):
        index = skip_command_prefix(segment)
        if index >= len(segment):
            continue
        command = normalize_command(segment[index])
        if not command:
            continue
        invocation = CommandInvocation(command=command, args=tuple(segment[index + 1:]))
        invocations.append(invocation)
        for payload in nested_payloads(invocation):
            invocations.extend(command_invocations(payload, depth + 1))
    for payload in command_substitution_payloads(cmd_line):
        invocations.extend(command_invocations(payload, depth + 1))
    return invocations


def command_matches(pattern: str, command: str) -> bool:
    target = normalize_command(pattern)
    if not target:
        return False
    return command == target or command.startswith(f"{target}.")


def rule_matches(rule: YoloRule, cmd_line: str) -> bool:
    if rule.match_type == "command":
        commands = [invocation.command for invocation in command_invocations(cmd_line)]
        return any(command_matches(pattern, command) for pattern in rule.patterns for command in commands)
    if rule.match_type == "regex":
        return any(re.search(pattern, cmd_line) for pattern in rule.patterns)
    if rule.match_type == "glob":
        return any(fnmatch.fnmatch(cmd_line, pattern) for pattern in rule.patterns)
    if rule.match_type == "contains":
        return any(pattern in cmd_line for pattern in rule.patterns)
    return False


def normalize_match_list(value: Any, context: str) -> tuple[str, ...]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise ValueError(f"{context}.match must be a string or list of strings")
    patterns = tuple(item for item in items if isinstance(item, str) and item)
    if len(patterns) != len(items) or not patterns:
        raise ValueError(f"{context}.match must contain non-empty strings")
    return patterns


def validate_rules(raw: Any, path: Path = YOLO_RULES_PATH, source: str = "file") -> YoloRuleset:
    if not isinstance(raw, dict):
        raise ValueError("rules file must contain a YAML mapping")
    default_action = raw.get("default", "ask")
    if not isinstance(default_action, str) or default_action not in RULE_ACTIONS:
        raise ValueError(f"default must be one of: {', '.join(sorted(RULE_ACTIONS))}")
    raw_rules = raw.get("rules", [])
    if raw_rules is None:
        raw_rules = []
    if not isinstance(raw_rules, list):
        raise ValueError("rules must be a list")
    rules: list[YoloRule] = []
    for index, item in enumerate(raw_rules):
        context = f"rules[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{context} must be a mapping")
        name = item.get("name", f"rule {index + 1}")
        match_type = item.get("type")
        action = item.get("action")
        risk = item.get("risk", "")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{context}.name must be a non-empty string")
        if match_type not in RULE_MATCH_TYPES:
            raise ValueError(f"{context}.type must be one of: {', '.join(sorted(RULE_MATCH_TYPES))}")
        if action not in RULE_ACTIONS:
            raise ValueError(f"{context}.action must be one of: {', '.join(sorted(RULE_ACTIONS))}")
        if risk is not None and not isinstance(risk, str):
            raise ValueError(f"{context}.risk must be a string")
        patterns = normalize_match_list(item.get("match"), context)
        if match_type == "regex":
            for pattern in patterns:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError(f"{context}.match regex error: {exc}") from exc
        rules.append(YoloRule(name=name.strip(), match_type=match_type, patterns=patterns, action=action, risk=(risk or "").strip()))
    return YoloRuleset(default_action=default_action, rules=tuple(rules), source=source, path=path, builtin=source == "built-in")


def parse_rule_text(content: str, path: Path = YOLO_RULES_PATH, source: str = "file") -> YoloRuleset:
    raw = yaml.safe_load(content)
    return validate_rules(raw, path=path, source=source)


def validate_rule_file_text(content: str, path: Path | None = None) -> YoloRuleset:
    return parse_rule_text(content, path=path or YOLO_RULES_PATH, source="file")


def is_rules_file_path(path: str | Path) -> bool:
    try:
        candidate = Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()
    except OSError:
        return False
    return candidate == active_rule_path()


def load_rules_file(path: Path) -> YoloRuleset:
    if not path.exists():
        return validate_rules(default_rule_data("approve"), path=path, source="built-in")
    return parse_rule_text(path.read_text(encoding="utf-8"), path=path, source="file")


def print_rule_error(error: str) -> None:
    global _LAST_ERROR_PRINTED
    if not error or error == _LAST_ERROR_PRINTED:
        return
    _LAST_ERROR_PRINTED = error
    print(f"YOLO rule load error: {error}", file=sys.stderr)


def cached_rules(force: bool = False) -> tuple[YoloRuleset | None, str]:
    path = active_rule_path()
    try:
        stat = path.stat()
        mtime_ns = stat.st_mtime_ns
    except OSError:
        mtime_ns = 0
    with _RULES_LOCK:
        if (
            not force
            and _RULES_CACHE.get("path") == path
            and _RULES_CACHE.get("mtime_ns") == mtime_ns
        ):
            return _RULES_CACHE.get("ruleset"), str(_RULES_CACHE.get("error") or "")
        try:
            ruleset = load_rules_file(path)
            error = ""
        except (OSError, yaml.YAMLError, ValueError) as exc:
            ruleset = None
            error = str(exc)
            print_rule_error(error)
        _RULES_CACHE.update({
            "path": path,
            "mtime_ns": mtime_ns,
            "ruleset": ruleset,
            "error": error,
        })
        return ruleset, error


def reload_rules() -> dict[str, Any]:
    with _RULES_LOCK:
        _RULES_CACHE["path"] = None
    return rules_status(force=True)


def hard_floor_decision(cmd_line: str) -> dict[str, Any] | None:
    if not cmd_line.strip():
        return None
    if FORK_BOMB_RE.search(cmd_line):
        return {"action": "block", "rule_name": "built-in hard floor: fork bomb", "risk": "process"}
    if BLOCK_DEVICE_REDIRECT_RE.search(cmd_line):
        return {"action": "block", "rule_name": "built-in hard floor: block-device redirect", "risk": "device"}
    for invocation in command_invocations(cmd_line):
        command = invocation.command
        args = list(invocation.args)
        if command == "mkfs" or command.startswith("mkfs."):
            return {"action": "block", "rule_name": "built-in hard floor: mkfs", "risk": "format"}
        if command == "dd" and any(BLOCK_DEVICE_RE.search(arg) or BLOCK_DEVICE_RE.search(arg.split("=", 1)[-1]) for arg in args):
            return {"action": "block", "rule_name": "built-in hard floor: dd block device", "risk": "device"}
        if command == "rm" and rm_targets_root(args):
            return {"action": "block", "rule_name": "built-in hard floor: rm root", "risk": "delete"}
        if command == "rmdir" and any(arg == "/" for arg in args):
            return {"action": "block", "rule_name": "built-in hard floor: rmdir root", "risk": "delete"}
    return None


def rm_targets_root(args: list[str]) -> bool:
    flags = "".join(arg[1:] for arg in args if arg.startswith("-") and arg != "--")
    has_recursive_or_force = "r" in flags.lower() or "f" in flags.lower()
    has_root = any(arg == "/" for arg in args)
    return has_root and has_recursive_or_force


def evaluate_ruleset(cmd_line: str, ruleset: YoloRuleset) -> dict[str, Any]:
    for rule in ruleset.rules:
        if rule_matches(rule, cmd_line):
            return {
                "action": rule.action,
                "rule_name": rule.name,
                "risk": rule.risk or "unknown",
                "source": ruleset.source,
                "path": str(ruleset.path),
            }
    return {
        "action": ruleset.default_action,
        "rule_name": "default",
        "risk": "unknown",
        "source": ruleset.source,
        "path": str(ruleset.path),
    }


def evaluate(cmd: str, prompt_type: str = "bash", agent: str = "", session: str = "", dangerously_yolo: bool = False) -> dict[str, Any]:
    settings = yolo_settings()
    path = active_rule_path()
    dry_run = settings["dry_run"]
    floor = None if dangerously_yolo else hard_floor_decision(cmd)
    if floor:
        decision = {**floor, "source": "hard-floor", "path": str(path)}
    else:
        ruleset, error = cached_rules()
        if error:
            decision = {
                "action": "ask",
                "rule_name": "ruleset error",
                "risk": "unknown",
                "source": "error",
                "path": str(path),
                "error": error,
            }
        elif ruleset is None:
            decision = {
                "action": "ask",
                "rule_name": "ruleset unavailable",
                "risk": "unknown",
                "source": "error",
                "path": str(path),
                "error": "ruleset unavailable",
            }
        else:
            decision = evaluate_ruleset(cmd, ruleset)
    decision["prompt_type"] = prompt_type
    decision["agent"] = agent
    decision["session"] = session
    decision["dry_run"] = dry_run
    if dry_run and decision.get("action") in ACTIVE_RULE_ACTIONS:
        decision["would_action"] = decision["action"]
        decision["action"] = "ask"
    return decision


def rules_status(force: bool = False) -> dict[str, Any]:
    settings = yolo_settings()
    path = active_rule_path()
    ruleset, error = cached_rules(force=force)
    try:
        stat = path.stat()
        mtime_ns = stat.st_mtime_ns
    except OSError:
        mtime_ns = 0
    return {
        "path": str(path),
        "display_path": YOLO_RULES_DISPLAY_PATH,
        "exists": path.exists(),
        "source": ruleset.source if ruleset else "error",
        "default": ruleset.default_action if ruleset else "ask",
        "rule_count": len(ruleset.rules) if ruleset else 0,
        "mtime_ns": mtime_ns,
        "dry_run": settings["dry_run"],
        "error": error,
    }
