from __future__ import annotations

import argparse
import re
import shutil
import socket
import ssl
import subprocess
import sys
from pathlib import Path

from .app import TmuxWebtermApp
from .common import AUTH_CONFIG_DISPLAY_PATH
from .common import SERVER_HOSTNAME
from .common import STATE_DIR
from .common import auth_setup_required
from .common import default_session_names
from .common import split_csv
from .common import unique_session_names
from .server import TmuxWebtermHTTPServer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Attach local tmux sessions in a browser.")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="interface to bind. Default 0.0.0.0 (all interfaces), on purpose: the product is built for "
             "reaching sessions from a phone or another machine on a trusted LAN, and every request is "
             "gated by the login layer. Pass --host 127.0.0.1 to restrict to localhost and tunnel in.",
    )
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
    parser.add_argument(
        "--self-signed",
        "--https-self-signed",
        dest="self_signed",
        action="store_true",
        help="serve HTTPS with an auto-generated self-signed certificate",
    )
    parser.add_argument("--cert", type=Path, default=None, help="TLS certificate PEM path")
    parser.add_argument("--key", type=Path, default=None, help="TLS private key PEM path")
    parser.add_argument("--print-transcripts", action="store_true")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="dev mode: backend re-execs on yolomux_lib/*.py change and the page auto-reloads when the "
        "static bundle changes (off by default; never enable for production)",
    )
    return parser.parse_args()


def start_dev_backend_watcher() -> None:
    """Dev-velocity #1c: re-exec the server when a backend source file changes, so a Python edit takes
    effect without the manual systemd-run restart dance. Daemon thread; only started under --dev."""
    import os
    import threading
    import time as _time

    repo_root = Path(__file__).resolve().parents[1]
    watched = [repo_root / "yolomux.py", repo_root / "tmux_wall.py", *sorted((repo_root / "yolomux_lib").glob("*.py"))]

    def snapshot() -> dict[str, int]:
        stamps: dict[str, int] = {}
        for path in watched:
            try:
                stamps[str(path)] = path.stat().st_mtime_ns
            except OSError:
                stamps[str(path)] = 0
        return stamps

    def loop() -> None:
        last = snapshot()
        while True:
            _time.sleep(0.5)
            now = snapshot()
            if now != last:
                changed = sorted(k for k in now if now.get(k) != last.get(k))
                print(f"[dev] backend change ({len(changed)} file(s)) — re-execing", flush=True)
                os.execv(sys.executable, [sys.executable, *sys.argv])

    threading.Thread(target=loop, name="dev-backend-watcher", daemon=True).start()


def self_signed_cert_paths() -> tuple[Path, Path]:
    tls_dir = STATE_DIR / "tls"
    return tls_dir / "self-signed.crt", tls_dir / "self-signed.key"


def self_signed_san() -> str:
    names = ["DNS:localhost", "IP:127.0.0.1"]
    if SERVER_HOSTNAME and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", SERVER_HOSTNAME):
        if SERVER_HOSTNAME != "localhost":
            names.append(f"DNS:{SERVER_HOSTNAME}")
    try:
        for iface_addrs in socket.getaddrinfo(socket.gethostname(), None):
            ip = iface_addrs[4][0]
            entry = f"IP:{ip}"
            if entry not in names:
                names.append(entry)
    except OSError:
        pass
    return ",".join(names)


def ensure_self_signed_cert() -> tuple[Path, Path]:
    cert_path, key_path = self_signed_cert_paths()
    if cert_path.exists() and key_path.exists():
        cert_path.chmod(0o600)
        key_path.chmod(0o600)
        return cert_path, key_path

    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError("--self-signed requires openssl; Python's standard library cannot create X.509 certificates")

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.parent.chmod(0o700)
    for path in (cert_path, key_path):
        if path.exists():
            path.unlink()

    command = [
        openssl,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-sha256",
        "-days",
        "3650",
        "-nodes",
        "-keyout",
        str(key_path),
        "-out",
        str(cert_path),
        "-subj",
        "/CN=YOLOmux self-signed",
        "-addext",
        f"subjectAltName={self_signed_san()}",
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or str(error)).strip()
        raise RuntimeError(f"failed to generate self-signed certificate: {detail}") from error

    cert_path.chmod(0o600)
    key_path.chmod(0o600)
    return cert_path, key_path


def tls_cert_key_paths(args: argparse.Namespace) -> tuple[Path | None, Path | None, bool]:
    if bool(args.cert) != bool(args.key):
        raise ValueError("--cert and --key must be provided together")
    if args.self_signed and (args.cert or args.key):
        raise ValueError("--self-signed cannot be combined with --cert/--key")
    if args.cert and args.key:
        return args.cert, args.key, False
    if args.self_signed:
        cert_path, key_path = ensure_self_signed_cert()
        return cert_path, key_path, True
    return None, None, False


def tls_context_for_args(args: argparse.Namespace) -> tuple[ssl.SSLContext | None, str]:
    cert_path, key_path, generated = tls_cert_key_paths(args)
    if not cert_path or not key_path:
        return None, ""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    if generated:
        return context, f"Using self-signed HTTPS certificate {cert_path}"
    return context, f"Using HTTPS certificate {cert_path}"


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


def print_auth_setup_error() -> None:
    print(
        f"You need to set {AUTH_CONFIG_DISPLAY_PATH} before using this program.",
        file=sys.stderr,
    )
    print(
        "Uncomment and edit the YAML account entries, then refresh the browser.",
        file=sys.stderr,
    )
    print(
        "Highly recommend browser login with HTTPS, for example --self-signed.",
        file=sys.stderr,
    )


def main() -> int:
    args = parse_args()
    try:
        tls_context, tls_message = tls_context_for_args(args)
    except (OSError, RuntimeError, ValueError, ssl.SSLError) as error:
        print(f"TLS setup failed: {error}", file=sys.stderr)
        return 2

    sessions = unique_session_names(split_csv(args.sessions)) if args.sessions is not None else default_session_names()
    app = TmuxWebtermApp(sessions, dangerously_yolo=args.dangerously_yolo)

    if args.print_transcripts:
        try:
            if auth_setup_required():
                print_auth_setup_error()
                return 2
            return print_transcripts(app)
        finally:
            app.stop_auto_approve_all()

    server = TmuxWebtermHTTPServer((args.host, args.port), app, tls_context=tls_context, dev=args.dev)
    scheme = "https" if tls_context else "http"
    if args.dev:
        print("[dev] dev mode ON: backend re-execs on yolomux_lib/*.py change; page auto-reloads on bundle change")
        start_dev_backend_watcher()
    url_host = "localhost" if args.host in {"0.0.0.0", "::"} else args.host
    session_text = ", ".join(sessions) if sessions else "no tmux sessions"
    print(f"Serving YOLOmux on {scheme}://{url_host}:{args.port}/ for {session_text}")
    if tls_message:
        print(tls_message)
    if args.dangerously_yolo:
        print("DANGEROUS YOLO mode is enabled: new Claude/Codex sessions bypass approval and sandbox protections.")
    if auth_setup_required():
        print("=" * 78)
        print(f"You need to set {AUTH_CONFIG_DISPLAY_PATH} before using this program.")
        print("YOLOmux created an inactive starter YAML file.")
        print("Leave users: as-is, then uncomment and edit one or more account entries before logging in.")
        if not tls_context:
            print(f"Highly recommend that you restart with HTTPS: python3 yolomux.py --port {args.port} --self-signed")
        print(f"YOLOmux is listening on {scheme}://{url_host}:{args.port}/ and will show this setup message in the browser.")
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
