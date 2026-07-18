from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time as _time
from pathlib import Path
from typing import Any

from .app import TmuxWebtermApp
from .background_owner import background_owner_priority
from .background_owner import read_background_owner_debug_status
from .common import AUTH_CONFIG_DISPLAY_PATH
from .common import SERVER_HOSTNAME
from .common import STATE_DIR
from .common import auth_setup_required
from .common import default_session_names
from .common import split_csv
from .common import unique_session_names
from .common import warn_unavailable_agent_commands_once
from .control import send_yolomux_control_request
from .server import TmuxWebtermHTTPServer
from .server_lease import acquire_server_port_lease
from .server_logs import emit_server_log
from .server_logs import install_server_log_handler


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
        help="launch Claude/Codex sessions with their dangerous permission, sandbox, and hook bypass flags",
    )
    parser.add_argument(
        "--self-signed",
        "--https-self-signed",
        dest="self_signed",
        action="store_true",
        help="serve HTTPS with an auto-generated self-signed certificate (the default; retained for compatibility)",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="serve plain HTTP instead of the default HTTPS (cannot be combined with --cert/--key)",
    )
    parser.add_argument("--cert", type=Path, default=None, help="TLS certificate PEM path")
    parser.add_argument("--key", type=Path, default=None, help="TLS private key PEM path")
    parser.add_argument("--print-transcripts", action="store_true")
    parser.add_argument("--print-background-owner", action="store_true", help="print the shared background-owner status JSON and exit")
    parser.add_argument("--print-runtime-report", action="store_true", help="print runtime owner/cache/endpoint/event/transcript diagnostics JSON and exit")
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


def self_signed_interface_ips() -> tuple[str, ...]:
    addresses: list[str] = []

    def add(value: str, interface: str = "") -> None:
        if interface.startswith(("docker", "br-", "veth")):
            return
        candidate = value.split("%", 1)[0]
        try:
            parsed = ipaddress.ip_address(candidate)
        except ValueError:
            return
        if parsed.is_loopback or parsed.is_unspecified or candidate in addresses:
            return
        addresses.append(candidate)

    for family, target in (
        (socket.AF_INET, ("10.255.255.255", 1)),
        (socket.AF_INET6, ("2001:db8::1", 1, 0, 0)),
    ):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as probe:
                probe.connect(target)
                add(probe.getsockname()[0])
        except OSError:
            pass
    try:
        for iface_addr in socket.getaddrinfo(socket.gethostname(), None):
            add(iface_addr[4][0])
    except OSError:
        pass
    ip_command = shutil.which("ip")
    if ip_command:
        try:
            result = subprocess.run(
                [ip_command, "-j", "address", "show"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for interface in json.loads(result.stdout):
                for addr_info in interface.get("addr_info", []):
                    add(str(addr_info.get("local", "")), str(interface.get("ifname", "")))
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, TypeError):
            pass
    ifconfig_command = shutil.which("ifconfig")
    if ifconfig_command:
        try:
            result = subprocess.run(
                [ifconfig_command],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            interface = ""
            for line in result.stdout.splitlines():
                if line and not line[0].isspace():
                    interface = line.split(":", 1)[0]
                for match in re.finditer(r"\binet6?\s+(?:addr:)?([^\s]+)", line):
                    add(match.group(1), interface)
        except (OSError, subprocess.CalledProcessError):
            pass
    return tuple(addresses)


def self_signed_san() -> str:
    names = ["DNS:localhost", "IP:127.0.0.1"]
    if SERVER_HOSTNAME and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", SERVER_HOSTNAME):
        if SERVER_HOSTNAME != "localhost":
            names.append(f"DNS:{SERVER_HOSTNAME}")
    for ip in self_signed_interface_ips():
        entry = f"IP:{ip}"
        if entry not in names:
            names.append(entry)
    return ",".join(names)


class SelfSignedCertificateUnavailable(RuntimeError):
    """The default certificate cannot be created on this host."""


def ensure_self_signed_cert() -> tuple[Path, Path]:
    cert_path, key_path = self_signed_cert_paths()
    if cert_path.exists() and key_path.exists():
        cert_path.chmod(0o600)
        key_path.chmod(0o600)
        return cert_path, key_path

    openssl = shutil.which("openssl")
    if not openssl:
        raise SelfSignedCertificateUnavailable(
            "openssl not found; the default self-signed HTTPS certificate cannot be created"
        )

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
    if getattr(args, "http", False) and (args.cert or args.key):
        raise ValueError("--http cannot be combined with --cert/--key")
    if args.cert and args.key:
        return args.cert, args.key, False
    if getattr(args, "http", False):
        return None, None, False
    cert_path, key_path = ensure_self_signed_cert()
    return cert_path, key_path, True


def tls_context_for_args(args: argparse.Namespace) -> tuple[ssl.SSLContext | None, str]:
    try:
        cert_path, key_path, generated = tls_cert_key_paths(args)
    except SelfSignedCertificateUnavailable as error:
        return (
            None,
            f"WARNING: {error}; starting plain HTTP. Install openssl, pass --cert/--key, or pass --http explicitly.",
        )
    if not cert_path or not key_path:
        return None, "WARNING: TLS disabled by --http; serving plain HTTP."
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    if generated:
        return context, (
            f"Using self-signed HTTPS certificate {cert_path} (SAN: {self_signed_san()}). "
            "Clients reaching this by an IP/hostname not in the SAN will get certificate errors; "
            "run tools/setup-tls.sh and import the CA on each client."
        )
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


def print_background_owner_status() -> int:
    print(json.dumps(read_background_owner_debug_status(), sort_keys=True, indent=2))
    return 0


def runtime_report_background_status() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    owner_debug = read_background_owner_debug_status()
    owner_control_response = send_yolomux_control_request(
        owner_debug.get("current_owner") if isinstance(owner_debug, dict) else None,
        {"action": "background_status"},
    )
    status = owner_control_response.get("status") if owner_control_response.get("ok") else None
    if not isinstance(status, dict):
        status = {
            "owner": False,
            "status": "unreachable",
            "current_owner": owner_debug.get("current_owner") if isinstance(owner_debug, dict) else None,
            "roles": {},
            "counters": {},
            "refresh_queue": {},
            "perf": {},
        }
    return owner_debug, owner_control_response, status


def print_runtime_report(sessions: list[str], dangerously_yolo: bool = False) -> int:
    app = TmuxWebtermApp(sessions, dangerously_yolo=dangerously_yolo)
    try:
        owner_debug, owner_control_response, background_status = runtime_report_background_status()
        payload = app.runtime_report_payload(
            background_status=background_status,
            owner_debug=owner_debug,
            owner_control_response=owner_control_response,
        )
        print(json.dumps(payload, sort_keys=True, indent=2))
        return 0
    finally:
        app.stop_auto_approve_all()
        app.control_server.stop()


def print_auth_setup_error() -> None:
    print(
        f"You need to set {AUTH_CONFIG_DISPLAY_PATH} before using this program.",
        file=sys.stderr,
    )
    print(
        "Uncomment and edit the YAML account entries, then refresh the browser.",
        file=sys.stderr,
    )


def main() -> int:
    args = parse_args()
    install_server_log_handler()
    warn_unavailable_agent_commands_once()
    if args.print_background_owner:
        return print_background_owner_status()
    sessions = unique_session_names(split_csv(args.sessions)) if args.sessions is not None else default_session_names()
    if args.print_runtime_report:
        return print_runtime_report(sessions, dangerously_yolo=args.dangerously_yolo)
    try:
        tls_context, tls_message = tls_context_for_args(args)
    except (OSError, RuntimeError, ValueError, ssl.SSLError) as error:
        print(f"TLS setup failed: {error}", file=sys.stderr)
        return 2

    lease = acquire_server_port_lease(args.port)
    if lease is None:
        print(f"YOLOmux port {args.port} is already owned by another server launch; refusing a duplicate.", file=sys.stderr)
        return 1

    app: TmuxWebtermApp | None = None
    server: TmuxWebtermHTTPServer | None = None
    try:
        app = TmuxWebtermApp(sessions, dangerously_yolo=args.dangerously_yolo)

        if args.print_transcripts:
            if auth_setup_required():
                print_auth_setup_error()
                return 2
            return print_transcripts(app)

        app.start_background_owner(port=args.port, priority=background_owner_priority(args.port))
        server = TmuxWebtermHTTPServer((args.host, args.port), app, tls_context=tls_context, dev=args.dev)
        if hasattr(app, "start_yoagent_backend_prewarm"):
            app.start_yoagent_backend_prewarm(reason="server_start")
        scheme = "https" if tls_context else "http"
        if args.dev:
            print("[dev] dev mode ON: backend re-execs on yolomux_lib/*.py change; page auto-reloads on bundle change")
            start_dev_backend_watcher()
        url_host = "localhost" if args.host in {"0.0.0.0", "::"} else args.host
        session_text = ", ".join(sessions) if sessions else "no tmux sessions"
        print(f"Serving YOLOmux on {scheme}://{url_host}:{args.port}/ for {session_text}")
        emit_server_log("info", "server", f"Serving YOLOmux on {scheme}://{url_host}:{args.port}/", category="lifecycle")
        if tls_message:
            print(tls_message)
        if args.dangerously_yolo:
            print("DANGEROUS YOLO mode is enabled: new Claude/Codex sessions bypass approval and sandbox protections.")
        if auth_setup_required():
            print("=" * 78)
            print(f"You need to set {AUTH_CONFIG_DISPLAY_PATH} before using this program.")
            print("YOLOmux created an inactive starter YAML file.")
            print("Leave users: as-is, then uncomment and edit one or more account entries before logging in.")
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
        return 0
    finally:
        if app is not None:
            app.stop_auto_approve_all()
        if server is not None:
            server.server_close()
        elif app is not None:
            if hasattr(app, "background_owner"):
                app.background_owner.stop()
            if hasattr(app, "control_server"):
                app.control_server.stop()
        lease.release()
