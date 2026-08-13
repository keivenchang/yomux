"""Immutable scenario and route types for the browser boot fixture."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True)
class BrowserBootRoute:
    path: str
    methods: tuple[str, ...]


BROWSER_BOOT_ROUTES = tuple(
    BrowserBootRoute(path, methods)
    for path, methods in (
        ("/api/chat/bootstrap", ("GET",)), ("/api/chat/delta", ("GET",)),
        ("/api/chat/page", ("GET",)), ("/api/chat/context", ("GET",)),
        ("/api/chat/search", ("GET",)), ("/api/chat/send", ("POST",)),
        ("/api/chat/yoagent", ("POST",)), ("/api/chat/typing", ("POST",)),
        ("/api/chat/read", ("POST",)), ("/api/yoagent/chat", ("POST",)),
        ("/api/settings", ("GET", "POST")), ("/api/notify", ("GET", "POST")),
        ("/api/create-session-plan", ("GET",)), ("/api/create-session", ("POST",)),
        ("/api/rename-session", ("POST",)), ("/api/ensure-session", ("POST",)),
        ("/api/attention-ack", ("POST",)), ("/api/auto-approve", ("GET", "POST")),
        ("/api/share", ("GET", "POST")), ("/api/session-metadata", ("GET",)),
        ("/api/transcripts", ("GET",)), ("/api/activity-summary", ("GET",)),
        ("/api/background/status", ("GET",)), ("/api/session-files", ("GET",)),
        ("/api/stats-capabilities", ("GET",)), ("/api/stats-observations", ("POST",)),
        ("/api/stats-snapshot", ("GET",)), ("/api/ping", ("GET",)),
        ("/api/event", ("POST",)), ("/api/events", ("GET",)),
        ("/api/logs", ("GET",)), ("/api/tmux-window", ("POST",)),
        ("/api/fs/list", ("GET",)), ("/api/fs/batch", ("POST",)),
    )
)


@dataclass(frozen=True)
class BrowserBootScenario:
    settings: Mapping = field(default_factory=lambda: MappingProxyType({}))
    transcript_current_path: str = "/home/test/yolomux.dev"
    transcript_git_root: str = "/home/test/yolomux.dev"
    session_files_payload: Mapping | None = None
    fs_entries: Mapping = field(default_factory=lambda: MappingProxyType({}))
    sessions: tuple[str, ...] = ("1",)
    transcript_sessions: Mapping = field(default_factory=lambda: MappingProxyType({}))
    session_files_payloads: Mapping = field(default_factory=lambda: MappingProxyType({}))
    terminal_css: str = ".terminal { width: 720px; height: 360px; }"
    grid_width: int = 1000
    grid_height: int = 620
    file_explorer_open_intent: str | None = None
    auto_approve_payload: Mapping | None = None
    access_role: str = "admin"
    auth_username: str = "alice"
    share_bootstrap: Mapping | None = None
    share_status_payload: Mapping | None = None
    wrap_app_root: bool = False
    yoagent_chat_mode: str | None = None
    available_agents: tuple[str, ...] | None = None
    agent_auth: Mapping | None = None
    background_status_payload: Mapping | None = None
    runtime_script_uri: str | None = None
    dangerously_yolo: bool = False
    hold_auto_approve: bool = False

    def __post_init__(self) -> None:
        for name in ("settings", "fs_entries", "transcript_sessions", "session_files_payloads", "session_files_payload", "auto_approve_payload", "share_bootstrap", "share_status_payload", "agent_auth", "background_status_payload"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, MappingProxyType(dict(value)))


BROWSER_BOOT_PRESETS = MappingProxyType({
    "default": BrowserBootScenario(),
    "readonly": BrowserBootScenario(access_role="readonly"),
    "share-view": BrowserBootScenario(access_role="readonly", auth_username="", share_bootstrap=MappingProxyType({"view": True})),
})

PRODUCTION_BOOTSTRAP_UNSUPPORTED_BY_BROWSER_FIXTURE = frozenset({"activitySummary", "agentLaunchCommands", "clientRevision", "dev", "devBundleRevision", "linearIssueBaseUrl", "recentSessions", "serverStartedAt", "serverStartedAtMs", "terminalCommands", "versionCommit", "versionCommitCount"})
PRODUCTION_BOOTSTRAP_OPTIONAL_IN_BROWSER_FIXTURE = frozenset({"agentAuth", "share"})
