# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""YO!agent transport providers.

The transport layer separates "how to deliver a prompt" from YO!agent routing. Structured
providers can own real agent conversations; tmux remains a labeled legacy fallback for panes that
already exist outside YO!agent's control.
"""

from __future__ import annotations

import asyncio
import os
import importlib
import importlib.util
import json
import selectors
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..common import PROJECT_ROOT
from ..common import YOLOMUX_VERSION
from ..common import codex_exec_argv
from ..common import codex_runtime_env
from ..tmux_utils import cmd_error
from ..tmux_utils import tmux_paste_text
from ..transcripts import codex_event_text


TMUX_LEGACY_TRANSPORT_ID = "tmux-legacy"
TMUX_TRANSPORT_ALIASES = {"pane-paste", "visible-pane-paste-return", TMUX_LEGACY_TRANSPORT_ID}
CODEX_APP_SERVER_TIMEOUT_SECONDS = 120.0


def normalize_yoagent_transport_id(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return TMUX_LEGACY_TRANSPORT_ID
    if text in TMUX_TRANSPORT_ALIASES:
        return TMUX_LEGACY_TRANSPORT_ID
    return text


def json_rpc_request(request_id: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


def json_rpc_notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = params
    return payload


def codex_app_server_initialize_params() -> dict[str, Any]:
    return {
        "clientInfo": {"name": "yolomux", "title": "YOLOmux", "version": YOLOMUX_VERSION},
        "capabilities": {"experimentalApi": True, "requestAttestation": False},
    }


def codex_app_server_thread_params(target: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "approvalPolicy": str(target.get("approval_policy") or target.get("approvalPolicy") or "on-request"),
        "approvalsReviewer": str(target.get("approvals_reviewer") or target.get("approvalsReviewer") or "user"),
        "sandbox": str(target.get("sandbox") or target.get("sandbox_mode") or "read-only"),
        "ephemeral": target.get("ephemeral") is not False,
    }
    cwd = str(target.get("cwd") or PROJECT_ROOT).strip()
    if cwd:
        params["cwd"] = cwd
    model = str(target.get("model") or target.get("agent_model") or "").strip()
    if model:
        params["model"] = model
    base_instructions = str(target.get("base_instructions") or "").strip()
    if base_instructions:
        params["baseInstructions"] = base_instructions
    return params


def codex_app_server_turn_params(thread_id: str, text: str, target: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "threadId": thread_id,
        "input": [{"type": "text", "text": text, "text_elements": []}],
    }
    cwd = str(target.get("cwd") or "").strip()
    if cwd:
        params["cwd"] = cwd
    return params


def codex_app_server_turn_text(turn: Any) -> str:
    if not isinstance(turn, dict):
        return ""
    items = turn.get("items")
    if not isinstance(items, list):
        return ""
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(part.strip() for part in parts if part.strip()).strip()


@dataclass(frozen=True)
class TransportDescription:
    id: str
    label: str
    kind: str
    implemented: bool
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class TransportSendResult:
    ok: bool
    sent: bool
    transport: str
    transport_label: str
    result_source: str
    text: str = ""
    error: str = ""
    pasted: bool = False
    returncode: int | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "sent": self.sent,
            "transport": self.transport,
            "transport_label": self.transport_label,
            "result_source": self.result_source,
        }
        if self.text:
            payload["text"] = self.text
        if self.error:
            payload["error"] = self.error
        if self.pasted:
            payload["pasted"] = True
        if self.returncode is not None:
            payload["returncode"] = self.returncode
        return payload


class AgentTransport:
    id = ""
    label = ""
    kind = "unknown"
    implemented = False
    capabilities: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()

    def describe(self) -> TransportDescription:
        return TransportDescription(
            id=self.id,
            label=self.label,
            kind=self.kind,
            implemented=self.implemented,
            capabilities=tuple(self.capabilities),
        )

    def discover(self) -> list[dict[str, Any]]:
        return []

    def can_send(self, target: dict[str, Any]) -> tuple[bool, str]:
        return False, "transport is not implemented"

    def describe_target(self, target: dict[str, Any]) -> dict[str, Any]:
        return {
            "transport": self.id,
            "transport_label": self.label,
            "transport_kind": self.kind,
            "transport_capabilities": list(self.capabilities),
            "session": str(target.get("session") or ""),
        }

    def send(self, target: dict[str, Any], text: str, *, submit: bool = True, **_kwargs: Any) -> TransportSendResult:
        return TransportSendResult(
            ok=False,
            sent=False,
            transport=self.id,
            transport_label=self.label,
            result_source="",
            error="transport is not implemented",
        )

    def watch_result(self, target: dict[str, Any], marker: dict[str, Any]) -> dict[str, Any]:
        return {"ok": False, "target": target, "marker": marker, "error": "transport does not support result watching"}

    def interrupt(self, target: dict[str, Any]) -> dict[str, Any]:
        return {"ok": False, "target": target, "error": "transport does not support interruption"}


class PlaceholderTransport(AgentTransport):
    def __init__(self, transport_id: str, label: str, kind: str, capabilities: tuple[str, ...]):
        self.id = transport_id
        self.label = label
        self.kind = kind
        self.capabilities = capabilities
        self.implemented = False


def optional_python_module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


# Managed SDK Python package per chat provider. These are the `optional_python_module_available`
# guards used by ClaudeSdkTransport / CodexSdkTransport; the diagnostic reuses the SAME names so the
# "module not installed" classification cannot drift from what those transports actually require.
YOAGENT_PROVIDER_SDK_MODULES = {
    "claude": "claude_code_sdk",
    "codex": "openai_codex",
}

# Structured reason codes for "why is no AI backend answering YO!agent chat". The UI maps each code to
# a specific, actionable locale string instead of the single generic det.noBackend fallback.
BACKEND_REASON_AVAILABLE = "available"
BACKEND_REASON_NO_PROVIDER = "no-provider"
BACKEND_REASON_MODULE_MISSING = "module-missing"
BACKEND_REASON_NO_CREDENTIALS = "no-credentials"


@dataclass(frozen=True)
class BackendAvailability:
    """Why YO!agent's OWN chat can (or cannot) answer right now.

    `reason` is one of the BACKEND_REASON_* codes so the UI can be specific instead of always showing
    the generic det.noBackend string. `available` is True only when reason == BACKEND_REASON_AVAILABLE.
    """

    available: bool
    reason: str
    backend: str
    provider: str = ""
    sdk_module: str = ""
    login_command: str = ""
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "available": self.available,
            "reason": self.reason,
            "backend": self.backend,
        }
        if self.provider:
            payload["provider"] = self.provider
        if self.sdk_module:
            payload["sdk_module"] = self.sdk_module
        if self.login_command:
            payload["login_command"] = self.login_command
        if self.detail:
            payload["detail"] = self.detail
        return payload


def _provider_login_command(provider: str) -> str:
    return {"claude": "claude auth login", "codex": "codex login"}.get(provider, "")


def _classify_provider_backend(
    provider: str,
    auth_status: dict[str, dict[str, Any]],
    module_available: Any,
) -> BackendAvailability:
    """Classify one concrete provider (claude/codex) into available / module-missing / no-credentials.

    A provider can answer YO!agent chat through EITHER the installed CLI (logged in) OR the managed SDK
    package. So it is unusable only when both routes are unavailable, and the reason distinguishes a
    missing managed SDK (no CLI on PATH at all) from a present-but-not-logged-in CLI.
    """
    entry = auth_status.get(provider) if isinstance(auth_status, dict) else None
    entry = entry if isinstance(entry, dict) else {}
    cli_installed = bool(entry.get("installed"))
    cli_logged_in = bool(entry.get("logged_in"))
    sdk_module = YOAGENT_PROVIDER_SDK_MODULES.get(provider, "")
    sdk_installed = bool(sdk_module) and bool(module_available(sdk_module))
    if (cli_installed and cli_logged_in) or sdk_installed:
        return BackendAvailability(
            available=True,
            reason=BACKEND_REASON_AVAILABLE,
            backend=provider,
            provider=provider,
            sdk_module=sdk_module if sdk_installed else "",
        )
    if not cli_installed and not sdk_installed:
        # No CLI on PATH and no managed SDK package: the host has nothing to run this provider with.
        return BackendAvailability(
            available=False,
            reason=BACKEND_REASON_MODULE_MISSING,
            backend=provider,
            provider=provider,
            sdk_module=sdk_module,
            detail=f"neither the {provider} CLI nor the `{sdk_module}` managed SDK package is installed",
        )
    # CLI is installed (or SDK route exists) but it is not authenticated: credentials/login are missing.
    return BackendAvailability(
        available=False,
        reason=BACKEND_REASON_NO_CREDENTIALS,
        backend=provider,
        provider=provider,
        sdk_module=sdk_module,
        login_command=_provider_login_command(provider),
        detail=f"the {provider} CLI is installed but not logged in",
    )


def backend_availability(
    backend: str,
    auth_status: dict[str, dict[str, Any]] | None = None,
    *,
    module_available: Any | None = None,
) -> BackendAvailability:
    """Report WHY YO!agent's own chat backend is (un)available, as a structured reason.

    Today the UI shows the generic det.noBackend string regardless of cause. This returns which
    precondition failed so the caller can be specific:
      - BACKEND_REASON_NO_PROVIDER: the `deterministic` backend is selected (no AI provider chosen), or
        `auto` resolved to nothing because no provider is both installed and logged in.
      - BACKEND_REASON_MODULE_MISSING: the chosen provider has neither its CLI on PATH nor its managed
        SDK package (`claude_code_sdk` / `openai_codex`) installed.
      - BACKEND_REASON_NO_CREDENTIALS: the chosen provider is installed but not logged in.
      - BACKEND_REASON_AVAILABLE: a provider can answer.

    `backend` is the resolved yoagent.backend preference (`auto` / `codex` / `claude` / `deterministic`).
    `auth_status` is the {provider: {installed, logged_in}} map from workdir.agent_auth_status(); it is
    injected so this stays a pure, testable function with no subprocess probing of its own.
    """
    selected = str(backend or "").strip().lower() or "deterministic"
    auth_status = auth_status if isinstance(auth_status, dict) else {}
    check_module = module_available or optional_python_module_available
    if selected in {"deterministic", ""}:
        return BackendAvailability(
            available=False,
            reason=BACKEND_REASON_NO_PROVIDER,
            backend="deterministic",
            detail="yoagent.backend is set to deterministic; no AI provider is selected",
        )
    if selected == "auto":
        # auto prefers codex, then claude. Surface the first provider that can answer; if none can,
        # report the most actionable blocker (a present-but-logged-out CLI beats a wholly missing one).
        results = [_classify_provider_backend(provider, auth_status, check_module) for provider in ("codex", "claude")]
        for result in results:
            if result.available:
                return result
        for result in results:
            if result.reason == BACKEND_REASON_NO_CREDENTIALS:
                return result
        if results:
            return results[0]
        return BackendAvailability(
            available=False,
            reason=BACKEND_REASON_NO_PROVIDER,
            backend="auto",
            detail="no AI provider is installed for the auto backend",
        )
    if selected in YOAGENT_PROVIDER_SDK_MODULES:
        return _classify_provider_backend(selected, auth_status, check_module)
    # An unknown backend value is treated as "no usable provider selected".
    return BackendAvailability(
        available=False,
        reason=BACKEND_REASON_NO_PROVIDER,
        backend=selected,
        detail=f"unknown yoagent.backend value `{selected}`",
    )


class TmuxLegacyTransport(AgentTransport):
    id = TMUX_LEGACY_TRANSPORT_ID
    label = "legacy tmux pane paste + Return"
    kind = "terminal"
    implemented = True
    aliases = tuple(sorted(TMUX_TRANSPORT_ALIASES - {TMUX_LEGACY_TRANSPORT_ID}))
    capabilities = ("visible-pane", "preflight", "paste-submit", "post-send-verify", "transcript-or-screen-result")

    def can_send(self, target: dict[str, Any]) -> tuple[bool, str]:
        if not str(target.get("pane_target") or "").strip():
            return False, "target pane is missing"
        return True, "target can be reached through legacy tmux pane paste"

    def describe_target(self, target: dict[str, Any]) -> dict[str, Any]:
        description = super().describe_target(target)
        description.update({
            "pane_target": str(target.get("pane_target") or ""),
            "agent_kind": str(target.get("agent_kind") or ""),
            "agent_transcript": str(target.get("agent_transcript") or ""),
        })
        return description

    def send(self, target: dict[str, Any], text: str, *, submit: bool = True, **kwargs: Any) -> TransportSendResult:
        paste_text = kwargs.get("tmux_paste_text") or tmux_paste_text
        result = paste_text(str(target.get("pane_target") or target.get("session") or ""), text, submit=submit)
        if result.returncode != 0:
            return TransportSendResult(
                ok=False,
                sent=False,
                transport=self.id,
                transport_label=self.label,
                result_source="",
                error=cmd_error(result, "tmux paste-buffer failed"),
                returncode=result.returncode,
            )
        return TransportSendResult(
            ok=True,
            sent=True,
            transport=self.id,
            transport_label=self.label,
            result_source="transcript-or-screen",
            pasted=True,
            returncode=result.returncode,
        )


class CodexExecTransport(AgentTransport):
    id = "codex-exec"
    label = "Codex exec JSONL"
    kind = "managed-one-shot"
    implemented = True
    capabilities = ("structured-jsonl", "final-message-file", "read-only-sandbox", "one-shot")

    def can_send(self, target: dict[str, Any]) -> tuple[bool, str]:
        if str(target.get("agent_kind") or "").strip().lower() != "codex":
            return False, "target is not a Codex agent"
        if not bool(target.get("managed")) and normalize_yoagent_transport_id(str(target.get("transport") or "")) != self.id:
            return False, "Codex exec is available only for YO!agent-managed targets"
        if not shutil.which("codex"):
            return False, "codex CLI not found"
        return True, "target can run through codex exec"

    def send(self, target: dict[str, Any], text: str, *, submit: bool = True, **kwargs: Any) -> TransportSendResult:
        can_send, reason = self.can_send(target)
        if not can_send:
            return TransportSendResult(ok=False, sent=False, transport=self.id, transport_label=self.label, result_source="", error=reason)
        timeout = float(kwargs.get("timeout") or 45.0)
        cwd = str(target.get("cwd") or PROJECT_ROOT)
        session_id = str(target.get("agent_session_id") or "").strip()
        with tempfile.TemporaryDirectory(prefix="yolomux-codex-exec-") as temp_dir:
            result_path = Path(temp_dir) / "last-message.txt"
            args = codex_exec_argv(
                resume_session_id=session_id or None,
                ephemeral=not bool(session_id),
                model=str(target.get("agent_model") or target.get("model") or "").strip() or None,
                effort=str(target.get("agent_effort") or target.get("effort") or "").strip() or None,
                service_tier=str(target.get("service_tier") or "").strip() or None,
            )
            if args and args[-1] == "-":
                args = [*args[:-1], "-o", str(result_path), args[-1]]
            else:
                args.extend(["-o", str(result_path)])
            run = kwargs.get("run") or subprocess.run
            try:
                completed = run(
                    args,
                    input=text,
                    cwd=cwd,
                    env=codex_runtime_env(),
                    text=True,
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return TransportSendResult(ok=False, sent=False, transport=self.id, transport_label=self.label, result_source="", error=str(exc))
            output_text = ""
            try:
                output_text = result_path.read_text(encoding="utf-8").strip()
            except OSError:
                output_text = ""
            if not output_text:
                parts: list[str] = []
                for line in str(completed.stdout or "").splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict):
                        piece = codex_event_text(event)
                        if piece:
                            parts.append(piece)
                output_text = "\n".join(parts).strip()
            if completed.returncode == 0 and output_text:
                return TransportSendResult(
                    ok=True,
                    sent=True,
                    transport=self.id,
                    transport_label=self.label,
                    result_source="codex-exec-jsonl",
                    text=output_text,
                    returncode=completed.returncode,
                )
            error = str(completed.stderr or "").strip() or f"codex exited {completed.returncode}"
            return TransportSendResult(ok=False, sent=False, transport=self.id, transport_label=self.label, result_source="codex-exec-jsonl", error=error, returncode=completed.returncode)


class ClaudeSdkTransport(AgentTransport):
    id = "claude-sdk"
    label = "Claude Agent SDK"
    kind = "managed-session"
    implemented = True
    capabilities = ("structured-stream", "multi-turn", "interrupt", "permissions")

    def can_send(self, target: dict[str, Any]) -> tuple[bool, str]:
        if normalize_yoagent_transport_id(str(target.get("transport") or "")) != self.id:
            return False, "Claude SDK requires an explicit managed transport target"
        if str(target.get("agent_kind") or "").strip().lower() != "claude":
            return False, "target is not a Claude agent"
        if not bool(target.get("managed")):
            return False, "Claude SDK is available only for YO!agent-managed targets"
        if not optional_python_module_available("claude_code_sdk"):
            return False, "claude-code-sdk Python package is not installed"
        return True, "target can run through the Claude Agent SDK"

    def _options(self, sdk: Any, target: dict[str, Any]) -> Any:
        kwargs: dict[str, Any] = {
            "cwd": str(target.get("cwd") or PROJECT_ROOT),
        }
        model = str(target.get("model") or target.get("agent_model") or "").strip()
        if model:
            kwargs["model"] = model
        session_id = str(target.get("thread_id") or target.get("agent_session_id") or "").strip()
        if session_id:
            kwargs["resume"] = session_id
        permission_mode = str(target.get("permission_mode") or target.get("permissionMode") or "").strip()
        if permission_mode:
            kwargs["permission_mode"] = permission_mode
        return sdk.ClaudeCodeOptions(**kwargs)

    async def _send_async(self, sdk: Any, target: dict[str, Any], text: str) -> str:
        assistant_parts: list[str] = []
        session_id = str(target.get("thread_id") or target.get("agent_session_id") or "default").strip() or "default"
        async with sdk.ClaudeSDKClient(options=self._options(sdk, target)) as client:
            await client.query(text, session_id=session_id)
            async for message in client.receive_response():
                if isinstance(message, sdk.AssistantMessage):
                    for block in message.content:
                        if isinstance(block, sdk.TextBlock):
                            assistant_parts.append(str(block.text or ""))
                elif isinstance(message, sdk.ResultMessage):
                    result_text = str(message.result or "").strip()
                    return result_text or "".join(assistant_parts).strip()
        return "".join(assistant_parts).strip()

    def send(self, target: dict[str, Any], text: str, *, submit: bool = True, **kwargs: Any) -> TransportSendResult:
        can_send, reason = self.can_send(target)
        if not can_send:
            return TransportSendResult(ok=False, sent=False, transport=self.id, transport_label=self.label, result_source="", error=reason)
        timeout = float(kwargs.get("timeout") or CODEX_APP_SERVER_TIMEOUT_SECONDS)
        sdk_error = RuntimeError
        try:
            sdk = importlib.import_module("claude_code_sdk")
            sdk_error = getattr(sdk, "ClaudeSDKError", RuntimeError)
            final_text = asyncio.run(asyncio.wait_for(self._send_async(sdk, target, text), timeout=timeout))
            if final_text:
                return TransportSendResult(ok=True, sent=True, transport=self.id, transport_label=self.label, result_source="claude-sdk", text=final_text)
            return TransportSendResult(ok=False, sent=True, transport=self.id, transport_label=self.label, result_source="claude-sdk", error="Claude SDK response completed without final text")
        except (OSError, RuntimeError, ValueError, TypeError, asyncio.TimeoutError, sdk_error) as exc:
            return TransportSendResult(ok=False, sent=False, transport=self.id, transport_label=self.label, result_source="claude-sdk", error=str(exc))


class ClaudeChannelsTransport(AgentTransport):
    id = "claude-channels"
    label = "Claude Channels"
    kind = "opt-in-visible-session"
    implemented = False
    capabilities = ("mcp-channel", "two-way-events", "visible-session", "allowlist")

    def can_send(self, target: dict[str, Any]) -> tuple[bool, str]:
        requested = normalize_yoagent_transport_id(str(target.get("transport") or ""))
        channel = str(target.get("channel") or target.get("channel_id") or target.get("channel_endpoint") or "").strip()
        if requested != self.id and not channel:
            return False, "Claude Channels requires an explicit channel-capable target"
        if str(target.get("agent_kind") or "").strip().lower() != "claude":
            return False, "target is not a Claude agent"
        if not channel:
            return False, "target Claude pane lacks a YOLOmux Claude Channel; use tmux-legacy"
        if not shutil.which("claude"):
            return False, "claude CLI not found"
        try:
            help_result = subprocess.run(["claude", "--help"], text=True, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"failed to inspect claude CLI channel support: {exc}"
        if "--channels" not in str(help_result.stdout or "") and "--channels" not in str(help_result.stderr or ""):
            return False, "installed claude CLI does not expose --channels"
        return False, "Claude Channels transport requires a YOLOmux channel MCP plugin implementation"

    def send(self, target: dict[str, Any], text: str, *, submit: bool = True, **kwargs: Any) -> TransportSendResult:
        _can_send, reason = self.can_send(target)
        return TransportSendResult(ok=False, sent=False, transport=self.id, transport_label=self.label, result_source="", error=reason)


class CodexSdkTransport(AgentTransport):
    id = "codex-sdk"
    label = "Codex SDK"
    kind = "managed-session"
    implemented = True
    capabilities = ("sdk", "multi-turn", "approvals", "streamed-events")

    def can_send(self, target: dict[str, Any]) -> tuple[bool, str]:
        if normalize_yoagent_transport_id(str(target.get("transport") or "")) != self.id:
            return False, "Codex SDK requires an explicit managed transport target"
        if str(target.get("agent_kind") or "").strip().lower() != "codex":
            return False, "target is not a Codex agent"
        if not bool(target.get("managed")):
            return False, "Codex SDK is available only for YO!agent-managed targets"
        if not optional_python_module_available("openai_codex"):
            return False, "openai-codex Python SDK is not installed"
        return True, "target can run through the Codex SDK"

    def _sandbox(self, sdk: Any, target: dict[str, Any]) -> Any:
        sandbox = str(target.get("sandbox") or target.get("sandbox_mode") or "read-only").strip()
        if sandbox == "workspace-write":
            return sdk.Sandbox.workspace_write
        if sandbox in {"danger-full-access", "full-access"}:
            return sdk.Sandbox.full_access
        return sdk.Sandbox.read_only

    def _approval_mode(self, sdk: Any, target: dict[str, Any]) -> Any:
        approval = str(target.get("approval_policy") or target.get("approvalPolicy") or "on-request").strip()
        if approval == "never":
            return sdk.ApprovalMode.deny_all
        return sdk.ApprovalMode.auto_review

    def send(self, target: dict[str, Any], text: str, *, submit: bool = True, **kwargs: Any) -> TransportSendResult:
        can_send, reason = self.can_send(target)
        if not can_send:
            return TransportSendResult(ok=False, sent=False, transport=self.id, transport_label=self.label, result_source="", error=reason)
        sdk_error = RuntimeError
        try:
            sdk = importlib.import_module("openai_codex")
            sdk_error = getattr(sdk, "CodexError", RuntimeError)
            cwd = str(target.get("cwd") or PROJECT_ROOT)
            codex_bin = str(target.get("codex_bin") or shutil.which("codex") or "").strip() or None
            config = sdk.CodexConfig(
                codex_bin=codex_bin,
                cwd=cwd,
                client_name="yolomux",
                client_title="YOLOmux",
                client_version=YOLOMUX_VERSION,
            )
            thread_id = str(target.get("thread_id") or target.get("agent_session_id") or "").strip()
            model = str(target.get("model") or target.get("agent_model") or "").strip() or None
            sandbox = self._sandbox(sdk, target)
            approval_mode = self._approval_mode(sdk, target)
            with sdk.Codex(config=config) as codex:
                if thread_id:
                    thread = codex.thread_resume(thread_id, approval_mode=approval_mode, cwd=cwd, model=model, sandbox=sandbox)
                else:
                    thread = codex.thread_start(approval_mode=approval_mode, cwd=cwd, model=model, sandbox=sandbox, ephemeral=target.get("ephemeral") is not False)
                result = thread.run(text, approval_mode=approval_mode, cwd=cwd, model=model, sandbox=sandbox)
            final_response = str(result.final_response or "").strip()
            if final_response:
                return TransportSendResult(ok=True, sent=True, transport=self.id, transport_label=self.label, result_source="codex-sdk", text=final_response)
            return TransportSendResult(ok=False, sent=True, transport=self.id, transport_label=self.label, result_source="codex-sdk", error="Codex SDK turn completed without a final response")
        except (OSError, RuntimeError, ValueError, TypeError, sdk_error) as exc:
            return TransportSendResult(ok=False, sent=False, transport=self.id, transport_label=self.label, result_source="codex-sdk", error=str(exc))


class CodexAppServerTransport(AgentTransport):
    id = "codex-app-server"
    label = "Codex app-server"
    kind = "managed-session"
    implemented = True
    capabilities = ("json-rpc", "multi-turn", "approvals", "streamed-events", "stdio")

    def can_send(self, target: dict[str, Any]) -> tuple[bool, str]:
        if normalize_yoagent_transport_id(str(target.get("transport") or "")) != self.id:
            return False, "Codex app-server requires an explicit managed transport target"
        if str(target.get("agent_kind") or "").strip().lower() != "codex":
            return False, "target is not a Codex agent"
        if not bool(target.get("managed")):
            return False, "Codex app-server is available only for YO!agent-managed targets"
        if not shutil.which("codex"):
            return False, "codex CLI not found"
        return True, "target can run through codex app-server stdio"

    def _write_message(self, process: subprocess.Popen[str], message: dict[str, Any]) -> None:
        if process.stdin is None:
            raise OSError("codex app-server stdin is closed")
        process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        process.stdin.flush()

    def _readline(self, process: subprocess.Popen[str], deadline: float) -> str:
        if process.stdout is None:
            raise OSError("codex app-server stdout is closed")
        timeout = max(0.0, deadline - time.monotonic())
        fileno: int | None = None
        try:
            fileno = process.stdout.fileno()
        except (OSError, ValueError):
            fileno = None
        if fileno is not None:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                if not selector.select(timeout):
                    raise subprocess.TimeoutExpired(["codex", "app-server"], timeout)
        line = process.stdout.readline()
        if not line:
            raise OSError("codex app-server exited without a JSON-RPC message")
        return line

    def _read_message(self, process: subprocess.Popen[str], deadline: float) -> dict[str, Any]:
        line = self._readline(process, deadline)
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OSError(f"codex app-server emitted invalid JSON-RPC: {exc}") from exc
        if not isinstance(message, dict):
            raise OSError("codex app-server emitted a non-object JSON-RPC message")
        return message

    def _read_response(self, process: subprocess.Popen[str], request_id: str, deadline: float, notifications: list[dict[str, Any]]) -> dict[str, Any]:
        while True:
            message = self._read_message(process, deadline)
            if str(message.get("id") or "") == request_id and ("result" in message or "error" in message):
                if message.get("error"):
                    raise OSError(f"codex app-server {request_id} failed: {message.get('error')}")
                result = message.get("result")
                return result if isinstance(result, dict) else {}
            notifications.append(message)

    def _terminate_process(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)

    def _wait_turn_complete(
        self,
        process: subprocess.Popen[str],
        thread_id: str,
        deadline: float,
        notifications: list[dict[str, Any]],
        on_event: Any | None = None,
    ) -> tuple[str, str]:
        deltas: list[str] = []
        while True:
            message = self._read_message(process, deadline)
            notifications.append(message)
            method = str(message.get("method") or "")
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            if message.get("id") is not None and method:
                return "", f"codex app-server requested client handling for `{method}`; approval relay is not implemented yet"
            if method == "item/agentMessage/delta":
                delta = params.get("delta")
                if isinstance(delta, str):
                    deltas.append(delta)
                    if callable(on_event):
                        on_event(
                            {
                                "event": "delta",
                                "thread_id": str(params.get("threadId") or ""),
                                "turn_id": str(params.get("turnId") or ""),
                                "item_id": str(params.get("itemId") or ""),
                                "delta": delta,
                                "text": "".join(deltas),
                            }
                        )
            elif method in {"item/reasoning/delta", "item/thinking/delta", "item/thought/delta"}:
                if callable(on_event):
                    on_event(
                        {
                            "event": "thinking",
                            "thread_id": str(params.get("threadId") or ""),
                            "turn_id": str(params.get("turnId") or ""),
                            "item_id": str(params.get("itemId") or ""),
                        }
                    )
            elif method == "turn/completed":
                if str(params.get("threadId") or "") != thread_id:
                    continue
                final_text = codex_app_server_turn_text(params.get("turn"))
                return final_text or "".join(deltas).strip(), ""

    def send(self, target: dict[str, Any], text: str, *, submit: bool = True, **kwargs: Any) -> TransportSendResult:
        can_send, reason = self.can_send(target)
        if not can_send:
            return TransportSendResult(ok=False, sent=False, transport=self.id, transport_label=self.label, result_source="", error=reason)
        timeout = float(kwargs.get("timeout") or CODEX_APP_SERVER_TIMEOUT_SECONDS)
        popen = kwargs.get("popen") or subprocess.Popen
        session = CodexAppServerSession(target, popen=popen, protocol=self)
        try:
            result, _status = session.send(text, target, timeout=timeout, on_event=kwargs.get("on_event"))
            return result
        finally:
            session.close()


class CodexAppServerSession:
    """Persistent stdio client for `codex app-server`.

    `CodexAppServerTransport` keeps its old one-shot semantics by creating and closing this helper
    per send. YO!agent chat keeps one instance alive so normal queries avoid CLI process startup.
    """

    def __init__(
        self,
        target: dict[str, Any],
        *,
        popen: Any | None = None,
        protocol: CodexAppServerTransport | None = None,
    ):
        self.target = dict(target)
        self.popen = popen or subprocess.Popen
        self.protocol = protocol or CodexAppServerTransport()
        self.process: subprocess.Popen[str] | None = None
        self.thread_id = ""
        self.request_counters: dict[str, int] = {}
        self.lock = threading.RLock()
        self.started_ts = 0.0

    def _request_id(self, prefix: str) -> str:
        self.request_counters[prefix] = self.request_counters.get(prefix, 0) + 1
        return f"{prefix}-{self.request_counters[prefix]}"

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def close(self) -> None:
        with self.lock:
            if self.process is not None:
                self.protocol._terminate_process(self.process)
            self.process = None

    def _start_process(self, target: dict[str, Any], deadline: float, notifications: list[dict[str, Any]]) -> dict[str, Any]:
        cwd = str(target.get("cwd") or PROJECT_ROOT)
        args = ["codex", "app-server", "--listen", "stdio://"]
        effort = str(target.get("agent_effort") or target.get("effort") or "").strip()
        if effort:
            args.extend(["-c", f'model_reasoning_effort="{effort}"'])
        service_tier = str(target.get("service_tier") or "").strip()
        if service_tier:
            args.extend(["-c", f'service_tier="{service_tier}"'])
        self.process = self.popen(
            args,
            cwd=cwd,
            env=codex_runtime_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.started_ts = time.time()
        initialize_id = self._request_id("initialize")
        self.protocol._write_message(self.process, json_rpc_request(initialize_id, "initialize", codex_app_server_initialize_params()))
        self.protocol._read_response(self.process, initialize_id, deadline, notifications)
        self.protocol._write_message(self.process, json_rpc_notification("initialized"))
        return {"process_started": True, "process_reused": False}

    def ensure_started(self, target: dict[str, Any] | None = None, *, timeout: float = CODEX_APP_SERVER_TIMEOUT_SECONDS) -> tuple[str, dict[str, Any]]:
        with self.lock:
            if target is not None:
                self.target = dict(target)
            deadline = time.monotonic() + max(1.0, float(timeout))
            notifications: list[dict[str, Any]] = []
            status: dict[str, Any] = {
                "transport": "codex-app-server",
                "persistent": True,
                "process_started": False,
                "process_reused": False,
                "thread_started": False,
                "thread_resumed": False,
            }
            requested_thread = str(self.target.get("thread_id") or self.target.get("agent_session_id") or "").strip()
            if requested_thread and self.thread_id and requested_thread != self.thread_id:
                self.close()
                self.thread_id = requested_thread
            if not self.alive():
                status.update(self._start_process(self.target, deadline, notifications))
            else:
                status["process_reused"] = True
            if self.thread_id:
                status["thread_id"] = self.thread_id
                return self.thread_id, status
            thread_response: dict[str, Any]
            if requested_thread:
                thread_id = self._request_id("thread")
                try:
                    params = {"threadId": requested_thread, **codex_app_server_thread_params(self.target)}
                    self.protocol._write_message(self.process, json_rpc_request(thread_id, "thread/resume", params))
                    thread_response = self.protocol._read_response(self.process, thread_id, deadline, notifications)
                    status["thread_resumed"] = True
                except OSError as exc:
                    status["resume_error"] = str(exc)
                    requested_thread = ""
            if not requested_thread:
                thread_id = self._request_id("thread")
                self.protocol._write_message(self.process, json_rpc_request(thread_id, "thread/start", codex_app_server_thread_params(self.target)))
                thread_response = self.protocol._read_response(self.process, thread_id, deadline, notifications)
                status["thread_started"] = True
            thread = thread_response.get("thread") if isinstance(thread_response.get("thread"), dict) else {}
            self.thread_id = str(thread.get("id") or requested_thread or "").strip()
            if not self.thread_id:
                raise OSError("codex app-server did not return a thread id")
            status["thread_id"] = self.thread_id
            return self.thread_id, status

    def send(
        self,
        text: str,
        target: dict[str, Any] | None = None,
        *,
        timeout: float = CODEX_APP_SERVER_TIMEOUT_SECONDS,
        on_event: Any | None = None,
    ) -> tuple[TransportSendResult, dict[str, Any]]:
        with self.lock:
            status: dict[str, Any] = {}
            turn_started = False
            try:
                deadline = time.monotonic() + max(1.0, float(timeout))
                thread_id, status = self.ensure_started(target, timeout=timeout)
                notifications: list[dict[str, Any]] = []
                turn_id = self._request_id("turn")
                self.protocol._write_message(self.process, json_rpc_request(turn_id, "turn/start", codex_app_server_turn_params(thread_id, text, self.target)))
                self.protocol._read_response(self.process, turn_id, deadline, notifications)
                turn_started = True
                final_text, error = self.protocol._wait_turn_complete(self.process, thread_id, deadline, notifications, on_event=on_event)
                if error:
                    return TransportSendResult(ok=False, sent=True, transport="codex-app-server", transport_label="Codex app-server", result_source="codex-app-server-json-rpc", error=error), status
                if final_text:
                    return TransportSendResult(ok=True, sent=True, transport="codex-app-server", transport_label="Codex app-server", result_source="codex-app-server-json-rpc", text=final_text), status
                return TransportSendResult(ok=False, sent=True, transport="codex-app-server", transport_label="Codex app-server", result_source="codex-app-server-json-rpc", error="codex app-server completed without a final agent message"), status
            except (OSError, subprocess.SubprocessError) as exc:
                self.close()
                return TransportSendResult(ok=False, sent=turn_started, transport="codex-app-server", transport_label="Codex app-server", result_source="codex-app-server-json-rpc", error=str(exc)), status


def codex_mcp_initialize_params() -> dict[str, Any]:
    return {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "yolomux", "version": YOLOMUX_VERSION},
    }


def codex_mcp_tool_result_text(result: dict[str, Any]) -> str:
    structured = result.get("structuredContent") if isinstance(result.get("structuredContent"), dict) else {}
    content = structured.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    items = result.get("content")
    if isinstance(items, list):
        parts = []
        for item in items:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(part.strip() for part in parts if part.strip()).strip()
    return ""


def claude_stream_json_argv(target: dict[str, Any]) -> list[str]:
    args = [
        "claude",
        "-p",
        "--verbose",
        "--input-format",
        "text",
        "--output-format",
        "stream-json",
    ]
    session_id = str(target.get("thread_id") or target.get("agent_session_id") or "").strip()
    if session_id:
        args.extend(["--resume", session_id])
    model = str(target.get("model") or target.get("agent_model") or "").strip()
    if model:
        args.extend(["--model", model])
    effort = str(target.get("agent_effort") or target.get("effort") or "").strip()
    if effort:
        args.extend(["--effort", effort])
    permission_mode = str(target.get("permission_mode") or target.get("permissionMode") or "").strip()
    if permission_mode:
        args.extend(["--permission-mode", permission_mode])
    return args


def claude_stream_json_result(stdout: str) -> tuple[str, str]:
    assistant_parts: list[str] = []
    for line in str(stdout or "").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type == "assistant":
            message = item.get("message") if isinstance(item.get("message"), dict) else {}
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                        assistant_parts.append(block["text"])
        elif item_type == "result":
            if item.get("is_error"):
                return "", str(item.get("result") or item.get("api_error_status") or "Claude stream-json result error")
            result = str(item.get("result") or "").strip()
            return result or "".join(assistant_parts).strip(), ""
    return "".join(assistant_parts).strip(), ""


class CodexMcpServerTransport(CodexAppServerTransport):
    id = "codex-mcp-server"
    label = "Codex MCP server"
    kind = "managed-tool-session"
    implemented = True
    capabilities = ("mcp-tools", "central-orchestration", "managed-session", "stdio")

    def can_send(self, target: dict[str, Any]) -> tuple[bool, str]:
        if normalize_yoagent_transport_id(str(target.get("transport") or "")) != self.id:
            return False, "Codex MCP server requires an explicit managed transport target"
        if str(target.get("agent_kind") or "").strip().lower() != "codex":
            return False, "target is not a Codex agent"
        if not bool(target.get("managed")):
            return False, "Codex MCP server is available only for YO!agent-managed targets"
        if not shutil.which("codex"):
            return False, "codex CLI not found"
        return True, "target can run through codex mcp-server stdio"

    def send(self, target: dict[str, Any], text: str, *, submit: bool = True, **kwargs: Any) -> TransportSendResult:
        can_send, reason = self.can_send(target)
        if not can_send:
            return TransportSendResult(ok=False, sent=False, transport=self.id, transport_label=self.label, result_source="", error=reason)
        timeout = float(kwargs.get("timeout") or CODEX_APP_SERVER_TIMEOUT_SECONDS)
        cwd = str(target.get("cwd") or PROJECT_ROOT)
        popen = kwargs.get("popen") or subprocess.Popen
        args = ["codex", "mcp-server"]
        process: subprocess.Popen[str] | None = None
        notifications: list[dict[str, Any]] = []
        try:
            process = popen(
                args,
                cwd=cwd,
                env=codex_runtime_env(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            deadline = time.monotonic() + max(1.0, timeout)
            self._write_message(process, json_rpc_request("initialize-1", "initialize", codex_mcp_initialize_params()))
            self._read_response(process, "initialize-1", deadline, notifications)
            self._write_message(process, json_rpc_notification("notifications/initialized"))
            self._write_message(process, json_rpc_request("tools-1", "tools/list", {}))
            tools_response = self._read_response(process, "tools-1", deadline, notifications)
            tools = tools_response.get("tools") if isinstance(tools_response.get("tools"), list) else []
            tool_names = {str(tool.get("name") or "") for tool in tools if isinstance(tool, dict)}
            thread_id = str(target.get("thread_id") or target.get("agent_session_id") or "").strip()
            tool_name = "codex-reply" if thread_id else "codex"
            if tool_name not in tool_names:
                return TransportSendResult(ok=False, sent=False, transport=self.id, transport_label=self.label, result_source="codex-mcp-json-rpc", error=f"codex MCP tool `{tool_name}` is not available")
            arguments: dict[str, Any] = {"prompt": text}
            if thread_id:
                arguments["threadId"] = thread_id
            else:
                arguments["cwd"] = cwd
                arguments["sandbox"] = str(target.get("sandbox") or target.get("sandbox_mode") or "read-only")
                arguments["approval-policy"] = str(target.get("approval_policy") or target.get("approvalPolicy") or "on-request")
                model = str(target.get("model") or target.get("agent_model") or "").strip()
                if model:
                    arguments["model"] = model
                base_instructions = str(target.get("base_instructions") or "").strip()
                if base_instructions:
                    arguments["base-instructions"] = base_instructions
                developer_instructions = str(target.get("developer_instructions") or "").strip()
                if developer_instructions:
                    arguments["developer-instructions"] = developer_instructions
            self._write_message(process, json_rpc_request("call-1", "tools/call", {"name": tool_name, "arguments": arguments}))
            call_response = self._read_response(process, "call-1", deadline, notifications)
            final_text = codex_mcp_tool_result_text(call_response)
            if final_text:
                return TransportSendResult(ok=True, sent=True, transport=self.id, transport_label=self.label, result_source="codex-mcp-json-rpc", text=final_text)
            return TransportSendResult(ok=False, sent=True, transport=self.id, transport_label=self.label, result_source="codex-mcp-json-rpc", error="codex MCP tool completed without content")
        except (OSError, subprocess.SubprocessError) as exc:
            return TransportSendResult(ok=False, sent=False, transport=self.id, transport_label=self.label, result_source="codex-mcp-json-rpc", error=str(exc))
        finally:
            if process is not None:
                self._terminate_process(process)


class ClaudeStreamJsonTransport(AgentTransport):
    id = "claude-stream-json"
    label = "Claude stream-json CLI"
    kind = "managed-stream"
    implemented = True
    capabilities = ("structured-stream", "one-shot", "resume", "result-message")

    def can_send(self, target: dict[str, Any]) -> tuple[bool, str]:
        if normalize_yoagent_transport_id(str(target.get("transport") or "")) != self.id:
            return False, "Claude stream-json requires an explicit managed transport target"
        if str(target.get("agent_kind") or "").strip().lower() != "claude":
            return False, "target is not a Claude agent"
        if not bool(target.get("managed")):
            return False, "Claude stream-json is available only for YO!agent-managed targets"
        if not shutil.which("claude"):
            return False, "claude CLI not found"
        return True, "target can run through claude stream-json"

    def send(self, target: dict[str, Any], text: str, *, submit: bool = True, **kwargs: Any) -> TransportSendResult:
        can_send, reason = self.can_send(target)
        if not can_send:
            return TransportSendResult(ok=False, sent=False, transport=self.id, transport_label=self.label, result_source="", error=reason)
        timeout = float(kwargs.get("timeout") or CODEX_APP_SERVER_TIMEOUT_SECONDS)
        cwd = str(target.get("cwd") or PROJECT_ROOT)
        run = kwargs.get("run") or subprocess.run
        args = claude_stream_json_argv(target)
        try:
            completed = run(
                args,
                input=text,
                cwd=cwd,
                env={**os.environ, "TERM": "xterm-256color", "NO_COLOR": "1"},
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return TransportSendResult(ok=False, sent=False, transport=self.id, transport_label=self.label, result_source="claude-stream-json", error=str(exc))
        output_text, output_error = claude_stream_json_result(str(completed.stdout or ""))
        if completed.returncode == 0 and output_text:
            return TransportSendResult(ok=True, sent=True, transport=self.id, transport_label=self.label, result_source="claude-stream-json", text=output_text, returncode=completed.returncode)
        error = output_error or str(completed.stderr or "").strip() or f"claude exited {completed.returncode}"
        return TransportSendResult(ok=False, sent=False, transport=self.id, transport_label=self.label, result_source="claude-stream-json", error=error, returncode=completed.returncode)


class AgentTransportRegistry:
    def __init__(self, transports: list[AgentTransport]):
        self._transports = transports
        self._by_id: dict[str, AgentTransport] = {}
        for transport in transports:
            self._by_id[transport.id] = transport
            for alias in transport.aliases:
                self._by_id[alias] = transport

    def ordered(self) -> list[AgentTransport]:
        return list(self._transports)

    def get(self, transport_id: str | None) -> AgentTransport:
        normalized = normalize_yoagent_transport_id(transport_id)
        return self._by_id.get(normalized) or self._by_id[TMUX_LEGACY_TRANSPORT_ID]

    def target_transport(self, target: dict[str, Any]) -> AgentTransport:
        return self.get(str(target.get("transport") or ""))

    def first_available(self, target: dict[str, Any]) -> AgentTransport:
        requested = str(target.get("transport") or "").strip()
        if requested:
            return self.get(requested)
        for transport in self._transports:
            can_send, _reason = transport.can_send(target)
            if can_send:
                return transport
        return self.get(TMUX_LEGACY_TRANSPORT_ID)

    def descriptions(self) -> list[dict[str, Any]]:
        return [transport.describe().__dict__ for transport in self._transports]


def default_yoagent_transport_registry() -> AgentTransportRegistry:
    return AgentTransportRegistry([
        ClaudeSdkTransport(),
        ClaudeChannelsTransport(),
        CodexSdkTransport(),
        CodexAppServerTransport(),
        CodexMcpServerTransport(),
        CodexExecTransport(),
        ClaudeStreamJsonTransport(),
        TmuxLegacyTransport(),
    ])
