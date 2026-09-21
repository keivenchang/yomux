#!/usr/bin/env python3
"""Authenticated launcher probes for session discovery."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import ssl
import sys
import time
import urllib.parse
import urllib.request
from typing import Any
from typing import Mapping

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from yolomux_lib.common import auth_cookie_value
from yolomux_lib.common import current_auth_users


def _row_cookie_header(port: int) -> dict[str, str]:
    users = [user for user in current_auth_users() if user.role == "admin"] or list(current_auth_users())
    if not users:
        return {}
    user = users[0]
    return {"Cookie": f"yolomux_auth_{port}={auth_cookie_value(user.username, user.password)}"}


def _get_json(scheme: str, host: str, port: int, path: str, headers: Mapping[str, str], *, timeout: float = 5.0) -> Any:
    url = f"{scheme}://{host}:{port}{path}"
    request = urllib.request.Request(url, headers=dict(headers))
    context = ssl._create_unverified_context() if scheme == "https" else None
    with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
        return json.load(response)


def _probe_sessions(args: argparse.Namespace) -> int:
    """Authenticated session discovery for one row."""

    expected = [name for name in args.sessions.split(",") if name]
    if not expected:
        print("sessions: no expected sessions supplied", file=sys.stderr)
        return 2
    headers = _row_cookie_header(args.port)
    last = ""
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        try:
            missing = []
            for session in expected:
                query = urllib.parse.urlencode({"session": session})
                payload = _get_json(
                    args.scheme,
                    args.host,
                    args.port,
                    f"/api/tmux-session-exists?{query}",
                    headers,
                    timeout=max(0.1, min(5.0, args.timeout)),
                )
                if payload.get("exists") is not True:
                    missing.append(session)
            if not missing:
                print(f"sessions {args.port}: all {len(expected)} visible")
                return 0
            last = "missing " + ",".join(missing)
        except (OSError, ValueError) as error:
            last = f"{type(error).__name__}: {error}"
        time.sleep(1)
    print(f"sessions {args.port} incomplete: {last}", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="launcher_probe", description="authenticated launcher probes")
    parser.add_argument("--scheme", choices=("https", "http"), default="https")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--timeout", type=float, default=60.0)
    sessions = parser.add_subparsers(dest="command", required=True).add_parser(
        "sessions", help="authenticated tmux-session discovery"
    )
    sessions.add_argument("--port", type=int, required=True)
    sessions.add_argument("--sessions", required=True, help="comma-separated expected session names")
    sessions.set_defaults(handler=_probe_sessions)
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
