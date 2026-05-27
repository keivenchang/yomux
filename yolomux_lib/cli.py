from __future__ import annotations

from .app import TmuxWebtermApp
from .core import *
from .server import TmuxWebtermHTTPServer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Attach local tmux sessions in a browser.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9998)
    parser.add_argument(
        "--sessions",
        nargs="*",
        default=None,
        help="tmux sessions, comma-separated or separate args. Default: current tmux sessions",
    )
    parser.add_argument(
        "--dang",
        "--dangerously-yolo",
        dest="dangerously_yolo",
        action="store_true",
        help="launch Claude/Codex sessions with their dangerous approval/sandbox bypass flags",
    )
    parser.add_argument("--print-transcripts", action="store_true")
    return parser.parse_args()


def print_transcripts(app: TmuxWebtermApp) -> int:
    payload = app.transcripts_payload()
    if payload["errors"]:
        for error in payload["errors"]:
            print(error, file=sys.stderr)
    for session, info in payload["sessions"].items():
        agents = info.get("agents", [])
        if not agents:
            print(f"{session}\t(no agent transcript found)")
            continue
        for agent in agents:
            transcript = agent.get("transcript") or f"ERROR: {agent.get('error')}"
            print(f"{session}\t{agent.get('kind')} pid={agent.get('pid')}\t{transcript}")
    return 1 if payload["errors"] else 0


def print_placeholder_auth_error() -> None:
    print(
        f"You need to set {AUTH_CONFIG_DISPLAY_PATH} before using this program.",
        file=sys.stderr,
    )
    print(
        f"Replace the placeholder {PLACEHOLDER_AUTH_USERNAME}/{PLACEHOLDER_AUTH_PASSWORD} credentials.",
        file=sys.stderr,
    )


def main() -> int:
    args = parse_args()
    sessions = unique_session_names(split_csv(args.sessions)) if args.sessions is not None else default_session_names()
    app = TmuxWebtermApp(sessions, dangerously_yolo=args.dangerously_yolo)

    if args.print_transcripts:
        if placeholder_auth_active():
            print_placeholder_auth_error()
            return 2
        return print_transcripts(app)

    server = TmuxWebtermHTTPServer((args.host, args.port), app)
    url_host = "localhost" if args.host in {"0.0.0.0", "::"} else args.host
    session_text = ", ".join(sessions) if sessions else "no tmux sessions"
    print(f"Serving YOLOmux on http://{url_host}:{args.port}/ for {session_text}")
    if args.dangerously_yolo:
        print("DANGEROUS YOLO mode is enabled: new Claude/Codex sessions bypass approval and sandbox protections.")
    if placeholder_auth_active():
        print("=" * 78)
        print(f"You need to set {AUTH_CONFIG_DISPLAY_PATH} before using this program.")
        print(f"Replace the placeholder {PLACEHOLDER_AUTH_USERNAME}/{PLACEHOLDER_AUTH_PASSWORD} credentials.")
        print(f"YOLOmux is listening on http://{url_host}:{args.port}/ and will show this setup message in the browser.")
        print("After saving auth.yaml, refresh the browser. No restart is required.")
        print("=" * 78)
    restored_auto = app.restore_auto_approve()
    if restored_auto:
        print(f"Restored YOLO for {', '.join(restored_auto)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        app.stop_auto_approve_all()
        server.server_close()
    return 0
