from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import ssl
import subprocess
import sys
import threading
import time as _time
from pathlib import Path
from typing import Any
from typing import Mapping

from .app import TmuxWebtermApp
from .backend_health.observer import BACKEND_HEALTH_OBSERVE_SECONDS
from .backend_health.observer import BackendHealthObserver
from .backend_health.store import BackendHealthDiagnostic
from .backend_health.store import BackendHealthStore
from .infra.background_owner import background_owner_priority
from .infra.background_owner import read_background_owner_debug_status
from .infra.common import _YOLOMUX_ROOTS
from .infra.common import AUTH_CONFIG_PATH
from .infra.common import SERVER_HOSTNAME
from .infra.common import STATE_DIR
from .infra.common import RUNTIME_DIR
from .infra.common import auth_setup_required
from .infra.common import default_session_names
from .infra.common import split_csv
from .infra.common import unique_session_names
from .infra.common import warn_unavailable_agent_commands_once
from .infra.root_paths import YolomuxRoots
from .infra.worktree_writer import server_start_writer_warning
from .control import send_yolomux_control_request
from .local_services.registry import bounded_process_table
from .local_services.registry import set_local_service_launch_context
from .local_services.registry import tracked_local_service_groups
from .local_services.registry import tracked_port_process_group
from .local_services.watchdog import GroupOverloadWatchdog
from .ptrace import allow_diagnostic_ptrace
from tools.tls_san import self_signed_interface_ips as discover_self_signed_interface_ips
from tools.tls_san import self_signed_san as build_self_signed_san
from .server import TmuxWebtermHTTPServer
from .server_lease import acquire_server_port_lease
from .server_logs import emit_server_log
from .server_logs import install_server_log_handler
from .tmux.tmux_utils import cmd_error
from tools.instance_isolation import ROOT_KEYS
from tools.instance_isolation import YOLOMUX_ROOT_ENV
from tools.instance_isolation import assert_early_port
from tools.instance_isolation import is_managed_instance_port


def _graceful_shutdown_signal(_signum: int, _frame: object) -> None:
    """Route TERM through the same cleanup path as an interactive interrupt."""
    raise KeyboardInterrupt


def configure_dang_diagnostic_ptrace(dangerously_yolo: bool) -> bool:
    """Enable ptrace only for an explicit --dang development launch and report the outcome."""
    if not dangerously_yolo:
        return False
    if allow_diagnostic_ptrace():
        message = "Development ptrace diagnostics are enabled by --dang (PR_SET_PTRACER_ANY)."
        print(message)
        emit_server_log("info", "server", message, category="diagnostics")
        return True
    message = "Development ptrace diagnostics are unavailable; continuing without diagnostic attach."
    print(message)
    emit_server_log("warning", "server", message, category="diagnostics")
    return False


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
    watched = [repo_root / "yolomux.py", repo_root / "tools" / "tmux_wall.py", *sorted((repo_root / "yolomux_lib").glob("*.py"))]

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
    return discover_self_signed_interface_ips()


def self_signed_san() -> str:
    return build_self_signed_san(SERVER_HOSTNAME, self_signed_interface_ips())


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
        detail = cmd_error(error, str(error))
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
    """Print runtime diagnostics via bounded record/socket lookups only.

    The old path constructed a second full TmuxWebtermApp just to render this
    JSON; on a loaded host that construction (SQLite opens, control-socket
    thread, forced transcript build) could stall indefinitely and leave stray
    children behind. A live server now answers over its existing control
    socket; with no live owner the report degrades to the durable ledger
    records instead of starting anything.
    """
    del sessions, dangerously_yolo  # the bounded report never constructs an app
    owner_debug = read_background_owner_debug_status()
    owner = owner_debug.get("current_owner") if isinstance(owner_debug, dict) else None
    response = send_yolomux_control_request(owner, {"action": "runtime_report"})
    report = response.get("report") if response.get("ok") else None
    if isinstance(report, dict):
        report.setdefault("owner_debug", owner_debug)
        print(json.dumps(report, sort_keys=True, indent=2))
        return 0
    table = bounded_process_table()
    port_groups = []
    for lease_path in sorted((RUNTIME_DIR / "server-leases").glob("*/*.lock")):
        try:
            port = int(lease_path.stem)
        except ValueError:
            continue
        group = tracked_port_process_group(port, RUNTIME_DIR, table)
        if group:
            port_groups.append(group)
    payload = {
        "mode": "bounded-records",
        "reason": "no live server answered the control socket; report built from ledger records only",
        "owner_debug": owner_debug,
        "owner_control_response": response,
        "port_groups": port_groups,
        "local_service_groups": tracked_local_service_groups(RUNTIME_DIR / "services", table),
    }
    print(json.dumps(payload, sort_keys=True, indent=2))
    return 0


STARTUP_WATCHDOG_SECONDS_ENV = "YOLOMUX_STARTUP_WATCHDOG_SECONDS"
STARTUP_WATCHDOG_DEFAULT_SECONDS = 120.0


def start_startup_overload_watchdog(port: int) -> threading.Thread | None:
    """Arm the bounded overload watchdog for the startup window only.

    A failed or wedged launch is when runaway groups have historically formed
    (2026-07-19 incident: the dev server plus two orphaned workers at ~300%
    CPU). The watchdog samples only the ledger-tracked group for this port and
    disarms itself when the window ends; set the env var to 0 to disable.
    """
    raw = os.environ.get(STARTUP_WATCHDOG_SECONDS_ENV, "")
    try:
        seconds = float(raw) if raw else STARTUP_WATCHDOG_DEFAULT_SECONDS
    except ValueError:
        seconds = STARTUP_WATCHDOG_DEFAULT_SECONDS
    if seconds <= 0:
        return None
    watchdog = GroupOverloadWatchdog(port=int(port), state_dir=RUNTIME_DIR, service_dir=RUNTIME_DIR / "services")
    thread = threading.Thread(
        target=watchdog.run,
        args=(seconds,),
        name=f"startup-overload-watchdog-{port}",
        daemon=True,
    )
    thread.start()
    return thread


BACKEND_HEALTH_OBSERVE_SECONDS_ENV = "YOLOMUX_BACKEND_HEALTH_OBSERVE_SECONDS"


def backend_health_label_source(app: TmuxWebtermApp) -> Any:
    """Return the callable that names a service the way the System row names it.

    Deliberately NOT a label map of its own. `system_status_service` owns the
    id -> capability-name table that the System row and Daemons roster already display, and a
    second table here is precisely the divergent copy that made watchd show up as the raw id in
    one place and "File watching" in another. The call is a pure function of the row it is handed;
    it reads no client and starts nothing.
    """

    def label(service: str) -> str:
        row = app.system_status_service({"service": str(service)})
        return str(row.get("label") or service)

    return label


def start_backend_health_observer(port: int, app: TmuxWebtermApp) -> BackendHealthObserver | None:
    """Arm the continuous backend-health observer for this leased port.

    Started here, after the port lease, because the retained history file is port-scoped and the
    lease is what makes it single-writer. It deliberately does NOT depend on the background-owner
    or stats-collector role, on an open System panel, or on any SSE subscriber: health has to be
    observed while every diagnostics panel is hidden, which is the whole point of the milestone.
    Set the env var to 0 to disable, matching the startup watchdog above.

    `main()` calls this AFTER `start_background_owner()` returns, so the election is DECIDED --
    either way -- and this process's statsd pin owner has been started, before the first cycle
    reads a row. Armed first, the first cycle beat the election by 2.4ms and published a
    `down` statsd that was simply not spawned yet; the measured ablation of both halves is in
    `app.STATSD_ABSENT_WHILE_PIN_PENDING`.

    The order is NOT conditional on the outcome. `start_background_owner()` returns True when
    this process wins and False when it loses or is blocked by an unreachable owner, and the
    observer is armed identically in every case, because a monitor that only runs on the
    winning process would be a worse defect than the flash it was reordered for.
    """

    raw = os.environ.get(BACKEND_HEALTH_OBSERVE_SECONDS_ENV, "")
    try:
        seconds = float(raw) if raw else BACKEND_HEALTH_OBSERVE_SECONDS
    except ValueError:
        seconds = BACKEND_HEALTH_OBSERVE_SECONDS
    if seconds <= 0:
        return None

    def report(diagnostic: BackendHealthDiagnostic) -> None:
        # The store and the observer both deduplicate into episodes through the one shared
        # `DiagnosticEpisodes`, so this is one row per episode and cannot become one row per
        # observation interval. `detail_text` carries the cause when the producer caught an
        # exception -- without it a monitor whose every cycle throws is a counter that only an
        # authenticated status request, or a process dump, can read.
        message = f"{diagnostic.code} ({diagnostic.detail_code or 'none'}) for port {diagnostic.port}"
        if diagnostic.detail_text:
            message = f"{message}\n{diagnostic.detail_text}"
        emit_server_log(
            "warning",
            "backend-health",
            message,
            category="lifecycle",
        )

    store = BackendHealthStore(int(port), on_diagnostic=report)
    # M8: the System projection reads this store's in-memory document instead of opening its
    # file on the HTTP request thread. Attached before start() so the very first request
    # after boot sees the loaded history rather than an unattached observer.
    app.attach_backend_health_store(store)
    observer = BackendHealthObserver(
        row_producers=app.local_services_row_producers,
        store=store,
        publish=app.client_events.publish,
        label_source=backend_health_label_source(app),
        interval_seconds=seconds,
        # The observer's supervisor boundary reports through the SAME reporter as the store's
        # persistence diagnostics. Without this line a cycle that throws on every interval is
        # recorded only in counters that `liveness()` reads, which is why the last one was found
        # with a process dump instead of by reading the server log.
        on_diagnostic=report,
        # M9: the recovery planner exists and is tested, but an observer built without a control
        # publishes `retry_blocked_no_control` for every verified-down service and never issues a
        # retry -- which is what every live server did until this line. The app owns the one map
        # from service id to that service's client `retry`; nothing here retries a service
        # itself, and the control's whole public surface is `retry`, so the observer cannot
        # reach a stop/restart/signal from the recovery path.
        recovery_control=app.local_services_recovery_control(),
    )
    # The liveness reader is attached BEFORE start(), so the first request after boot sees a
    # real "attached but no cycle completed yet" rather than "no observer at all" -- two facts
    # the panel distinguishes.
    app.attach_backend_health_observer(observer)
    observer.start()
    return observer


def print_auth_setup_error() -> None:
    print(
        f"You need to set {AUTH_CONFIG_PATH} before using this program.",
        file=sys.stderr,
    )
    print(
        "Uncomment and edit the YAML account entries, then refresh the browser.",
        file=sys.stderr,
    )


def startup_path_line(
    port: int,
    *,
    environ: Mapping[str, str] | None = None,
    roots: YolomuxRoots | None = None,
) -> str:
    """Describe path provenance using the early classifier and resolved root owner."""

    values = os.environ if environ is None else environ
    resolved = _YOLOMUX_ROOTS if roots is None else roots
    managed = is_managed_instance_port(port) if environ is None else is_managed_instance_port(port, values)
    if managed:
        root_description = (
            f"{resolved.root} (auto-derived for non-default port {port} "
            "because no root-family override was set)"
        )
    elif values.get(YOLOMUX_ROOT_ENV):
        root_description = f"{resolved.root} (explicit)"
    else:
        overrides = sorted(key for key in ROOT_KEYS if values.get(key))
        if overrides:
            verb = "was" if len(overrides) == 1 else "were"
            reason = f"{', '.join(overrides)} {verb} explicitly set"
        else:
            reason = "no managed instance root contract was applied"
        root_description = f"unset (auto-derivation skipped because {reason})"
    return (
        f"YOLOmux paths: YOLOMUX_ROOT={root_description}; config={resolved.config_dir}; "
        f"state={resolved.state_dir}; cache={resolved.cache_dir}; runtime={resolved.runtime_dir}"
    )


def report_worktree_writer_warning() -> bool:
    """Detect a foreign writer while preserving read-only server startup."""

    warning = server_start_writer_warning(Path(__file__).resolve().parents[1])
    if warning:
        print(f"WARNING: {warning}", file=sys.stderr)
        emit_server_log("warning", "worktree", warning, category="safety")
    return True


def main() -> int:
    args = parse_args()
    try:
        assert_early_port(args.port)
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    install_server_log_handler()
    configure_dang_diagnostic_ptrace(args.dangerously_yolo)
    warn_unavailable_agent_commands_once()
    if args.print_background_owner:
        return print_background_owner_status()
    sessions = unique_session_names(split_csv(args.sessions)) if args.sessions is not None else default_session_names()
    if args.print_runtime_report:
        return print_runtime_report(sessions, dangerously_yolo=args.dangerously_yolo)
    if not args.print_transcripts:
        print(startup_path_line(args.port), flush=True)
    report_worktree_writer_warning()
    try:
        tls_context, tls_message = tls_context_for_args(args)
    except (OSError, RuntimeError, ValueError, ssl.SSLError) as error:
        print(f"TLS setup failed: {error}", file=sys.stderr)
        return 2

    lease = acquire_server_port_lease(args.port)
    if lease is None:
        print(f"YOLOmux port {args.port} is already owned by another server launch; refusing a duplicate.", file=sys.stderr)
        return 1
    # Ledger provenance + bounded startup protection: services spawned from
    # here on are stamped with this port, and a runaway during the launch
    # window is contained by the tracked-group watchdog.
    set_local_service_launch_context(args.port)
    start_startup_overload_watchdog(args.port)

    app: TmuxWebtermApp | None = None
    server: TmuxWebtermHTTPServer | None = None
    backend_health: BackendHealthObserver | None = None
    try:
        app = TmuxWebtermApp(sessions, dangerously_yolo=args.dangerously_yolo)

        if args.print_transcripts:
            if auth_setup_required():
                print_auth_setup_error()
                return 2
            return print_transcripts(app)

        # Unconditional and outcome-independent: the election is decided first only so the
        # observer's first cycle cannot race it, never so the observer depends on winning it.
        app.start_background_owner(
            port=args.port,
            priority=background_owner_priority(args.port),
            managed_instance=is_managed_instance_port(args.port),
        )
        backend_health = start_backend_health_observer(args.port, app)
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
            print(f"You need to set {AUTH_CONFIG_PATH} before using this program.")
            print("YOLOmux created an inactive starter YAML file.")
            print("Leave users: as-is, then uncomment and edit one or more account entries before logging in.")
            print(f"YOLOmux is listening on {scheme}://{url_host}:{args.port}/ and will show this setup message in the browser.")
            print("After saving auth.yaml, refresh the browser. No restart is required.")
            print("=" * 78)
        restored_auto = app.restore_auto_approve()
        if restored_auto:
            print(f"Restored YOLO for {', '.join(restored_auto)}")
        previous_sigterm = signal.signal(signal.SIGTERM, _graceful_shutdown_signal)
        try:
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print("\nStopping.")
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)
        return 0
    finally:
        try:
            # Stopped FIRST, before any backend client is closed: a probe in flight against a
            # client that is being torn down would report a failure the user never had, and the
            # observer must not be the thing that keeps this process alive.
            if backend_health is not None:
                backend_health.stop()
        finally:
            try:
                if app is not None:
                    app.stop_auto_approve_all()
            finally:
                try:
                    if server is not None:
                        # server_close owns the client-event watcher and its RustNotify
                        # thread. It must run even if an earlier app cleanup failed, or
                        # CPython can finalize while that native thread is still alive.
                        server.server_close()
                    elif app is not None:
                        if hasattr(app, "background_owner"):
                            app.background_owner.stop()
                        if hasattr(app, "control_server"):
                            app.control_server.stop()
                finally:
                    lease.release()
