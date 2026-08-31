from http.client import HTTPConnection
import signal
import threading
import time
from types import SimpleNamespace

import pytest

from tests.tmux_runtime import adaptive_tmux_poll_interval
from tests.tmux_runtime import run_isolated_tmux
from tests.tmux_runtime import start_isolated_default_tmux_runtime
from tests.tmux_runtime import start_isolated_tmux_runtime
from tests.tmux_runtime import stop_isolated_tmux_runtime
from yolomux_lib import app as app_module
from yolomux_lib import tmux_signals
from yolomux_lib import tmux_utils
from yolomux_lib.server import TmuxWebtermHTTPServer
from yolomux_lib.tmux_signals import install_tmux_signal_monitoring
from yolomux_lib.tmux_signals import parse_tmux_signal_snapshot
from yolomux_lib.tmux_signals import parse_pane_signal_row
from yolomux_lib.tmux_signals import tmux_signal_subscription_commands
from yolomux_lib.tmux_signals import tmux_signal_hook_command
from yolomux_lib.tmux_signals import tmux_control_attach_command
from yolomux_lib.tmux_signals import tmux_control_event_relevant
from yolomux_lib.tmux_signals import tmux_control_event_type
from yolomux_lib.tmux_signals import window_record_key


def test_tmux_signal_watcher_status_preserves_typed_absence_states():
    watcher = tmux_signals.TmuxSignalEventWatcher(sessions=lambda: [], on_event=lambda event: None)

    assert watcher.status_payload() == {
        "state": "never-started",
        "healthy": False,
        "reason_code": "not_started",
        "reason": "Tmux signal watcher has not been started",
        "sessions": [],
        "thread_alive": False,
        "process_pid": 0,
    }

    watcher._set_status("attaching", sessions=["alpha"])
    attaching = watcher.status_payload()
    assert attaching["state"] == "attaching"
    assert attaching["healthy"] is None
    assert attaching["sessions"] == ["alpha"]

    watcher._set_status("no-sessions", sessions=[])
    no_sessions = watcher.status_payload()
    assert no_sessions["state"] == "no-sessions"
    assert no_sessions["healthy"] is True

    watcher._set_status("exited", error="tmux control-mode start failed: refused")
    exited = watcher.status_payload()
    assert exited["state"] == "exited"
    assert exited["healthy"] is False
    assert exited["reason_code"] == "control_client_exited"
    assert exited["reason"] == "tmux control-mode start failed: refused"


def test_non_filesystem_client_event_stream_does_not_start_watchd_until_a_descriptor_exists():
    release_worker = threading.Event()
    watchd_starts = []
    watchd_running = [False]
    fake_app = SimpleNamespace()
    fake_app.server_attention_ack_event_poll_seconds = lambda: 30.0
    fake_app.server_tmux_signal_event_poll_seconds = lambda: 30.0
    fake_app.client_event_watch_loop = lambda _record: release_worker.wait(2.0)
    fake_app.start_tmux_signal_event_watcher = lambda: None
    fake_app.replay_shared_background_client_events = lambda: None
    def start_watchd(record):
        if watchd_running[0]:
            return False
        watchd_running[0] = True
        watchd_starts.append(record)
        return True

    fake_app.start_watchd_revision_watcher = start_watchd
    bridge = app_module.WatchBridge(fake_app)

    bridge.start_client_event_watcher(fake_app)
    record = bridge.state.event_watcher_record
    assert record.worker is not None and record.worker.is_alive()
    assert watchd_starts == []

    # A descriptor is the demand transition. Re-entering through the same SSE lifecycle starts
    # watchd once; another subscriber cannot install a duplicate bridge worker.
    bridge.state.descriptors["browser-1"] = object()
    bridge.start_client_event_watcher(fake_app)
    bridge.start_client_event_watcher(fake_app)
    assert watchd_starts == [record]

    release_worker.set()
    record.worker.join(timeout=2.0)
    assert record.worker.is_alive() is False


def test_client_event_lifecycle_requires_a_live_tmux_control_client(
    monkeypatch,
    tmp_path,
    make_tmux_webterm_app,
    no_control_socket,
    isolated_yoagent_conversation_state,
):
    """An event-watch thread without a control client must not suppress fallback polling."""

    app = make_tmux_webterm_app(())
    try:
        app.start_client_event_watcher()
        assert app.tmux_signal_event_watcher is not None
        assert app.tmux_signal_event_watcher.thread is not None
        assert app.tmux_signal_event_watcher.thread.is_alive()
        assert app.tmux_signal_event_watcher_healthy() is False
    finally:
        app.stop_client_event_watcher()


def test_client_event_sse_lifecycle_recreates_a_stopped_tmux_control_client(
    monkeypatch,
    tmp_path,
    make_tmux_webterm_app,
    no_control_socket,
    isolated_yoagent_conversation_state,
):
    """A second SSE subscriber must repair a stopped control client under its live parent."""

    runtime = start_isolated_tmux_runtime(monkeypatch, tmp_path)
    monkeypatch.setenv("YOLOMUX_TEST_AUTH_BYPASS", "1")
    app = make_tmux_webterm_app(tuple(runtime.sessions))
    server = TmuxWebtermHTTPServer(("127.0.0.1", 0), app)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    first_connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    second_connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    rows = []
    try:
        first_connection.request("GET", "/api/client-events?channels=status")
        first_response = first_connection.getresponse()
        assert first_response.status == 200
        assert first_response.readline().decode("utf-8") == "event: ready\n"
        stopped_watcher = app.tmux_signal_event_watcher
        assert stopped_watcher is not None
        stopped_watcher.stop()
        assert stopped_watcher.thread is not None
        stopped_watcher.thread.join(timeout=2.0)
        assert stopped_watcher.thread.is_alive() is False

        second_connection.request("GET", "/api/client-events?channels=status")
        second_response = second_connection.getresponse()
        assert second_response.status == 200
        assert second_response.readline().decode("utf-8") == "event: ready\n"
        assert app.tmux_signal_event_watcher is not stopped_watcher
        assert app.tmux_signal_event_watcher.wait_for_status("attached", timeout=4.0) is True
        deadline = time.monotonic() + 4.0
        attempt = 0
        while time.monotonic() < deadline:
            result = run_isolated_tmux(runtime, "list-clients", "-F", "#{client_control_mode}\\t#{client_session}")
            rows = [line for line in result.stdout.splitlines() if line.startswith("1\\t")]
            if rows:
                break
            time.sleep(adaptive_tmux_poll_interval(attempt))
            attempt += 1
        assert rows == [f"1\\t{runtime.sessions[0]}"]
        assert app.tmux_signal_event_watcher_healthy() is True
    finally:
        first_connection.close()
        second_connection.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2.0)
        assert server_thread.is_alive() is False
        stop_isolated_tmux_runtime(runtime)


def test_readonly_control_attach_starts_on_fixture_default_server(monkeypatch, tmp_path):
    """The normal no-socket configuration must create an observable control client."""

    runtime = start_isolated_default_tmux_runtime(monkeypatch, tmp_path)
    watcher = tmux_signals.TmuxSignalEventWatcher(sessions=lambda: list(runtime.sessions), on_event=lambda _event: None)
    rows = []
    try:
        assert watcher.start() is True
        assert watcher.wait_for_status("attached", timeout=4.0) is True
        deadline = time.monotonic() + 4.0
        attempt = 0
        while time.monotonic() < deadline:
            result = run_isolated_tmux(runtime, "list-clients", "-F", "#{client_control_mode}\\t#{client_session}")
            rows = [line for line in result.stdout.splitlines() if line.startswith("1\\t")]
            if rows:
                break
            time.sleep(adaptive_tmux_poll_interval(attempt))
            attempt += 1
        assert rows == [f"1\\t{runtime.sessions[0]}"]
        assert watcher.status_payload()["state"] == "attached"
    finally:
        watcher.stop()
        assert watcher.thread is not None
        watcher.thread.join(timeout=2.0)
        assert watcher.thread.is_alive() is False
        stop_isolated_tmux_runtime(runtime)


def test_window_record_key_prefers_record_key_and_falls_back_to_session_window():
    assert window_record_key({"key": "alpha:2", "session": "ignored", "window_index": 9}) == "alpha:2"
    assert window_record_key({"session": "alpha", "window_index": 2}) == "alpha:2"
    assert window_record_key({"session_name": "beta", "window": "3"}) == "beta:3"


def test_parse_tmux_signal_snapshot_maps_window_and_pane_fields():
    windows_stdout = "\n".join([
        "alpha\t$1\t1710000010\t1710000000\t1\tclient-a\t0\t@1\tcodex\t1\t1710000100\t1\t0\t0\t2\tclient-a,client-b\t2\t120\t36\t0\tlayout-a\tvisible-a",
        "alpha\t$1\t1710000010\t1710000000\t1\tclient-a\t1\t@2\tbash\t0\t1710000001\t0\t1\t0\t0\t\t1\t80\t24\t1\tlayout-b\tvisible-b",
    ])
    panes_stdout = "\n".join([
        "alpha\t0\t@1\t0\t%1\t1\t/home/keivenc/project\tcodex\t\t0\t\t\t\t1\t0\t\t0\t0\t1234\t120\t36\t4000\t120000",
        "alpha\t1\t@2\t0\t%2\t1\t/home/keivenc/project\tbash\t\t1\t2\t\t1710000111\t0\t1\tcopy-mode\t1\t1\t4321\t80\t24\t12\t300",
    ])
    clients_stdout = "\n".join([
        "client-a\talpha\t1710000200\t120\t36\tattached,focused\t0\t0\tkeiven",
        "client-b\talpha\t1710000300\t80\t24\tattached\t0\t1\tkeiven",
        "control-a\talpha\t1710000400\t10\t10\tattached\t1\t1\tkeiven",
    ])

    payload = parse_tmux_signal_snapshot(windows_stdout, panes_stdout, clients_stdout, generated_at=12.5, compute_ms=3.25)

    assert payload["ok"] is True
    assert payload["generated_at"] == 12.5
    assert payload["compute_ms"] == 3.2
    assert payload["window_count"] == 2
    assert payload["pane_count"] == 2
    assert payload["client_count"] == 3
    assert payload["agent_count"] == 1
    first = payload["windows"][0]
    assert first["key"] == "alpha:0"
    assert first["session"] == "alpha"
    assert first["window_id"] == "@1"
    assert first["active"] is True
    assert first["activity_ts"] == 1710000100
    assert first["activity_flag"] is True
    assert first["bell_flag"] is False
    assert first["silence_flag"] is False
    assert first["active_clients"] == 2
    assert first["active_clients_list"] == "client-a,client-b"
    assert [client["name"] for client in first["active_client_details"]] == ["client-a", "client-b"]
    assert first["authoritative_client"] == {
        "client_name": "client-b",
        "client_user": "keiven",
        "activity_ts": 1710000300,
        "width": 80,
        "height": 24,
        "readonly": True,
        "flags": "attached",
        "reason": "most-recent-active-viewer",
    }
    assert first["pane_count"] == 2
    assert first["width"] == 120
    assert first["height"] == 36
    assert first["zoomed"] is False
    assert first["layout"] == "layout-a"
    assert first["visible_layout"] == "visible-a"
    pane = first["panes"][0]
    assert pane["pane_id"] == "%1"
    assert pane["target"] == "%1"
    assert pane["current_command"] == "codex"
    assert pane["agent"] == "codex"
    assert pane["alternate_on"] is True
    assert pane["dead"] is False
    assert pane["in_mode"] is False
    assert pane["history_size"] == 4000
    assert payload["agents"] == [{
        "session": "alpha",
        "window_index": "0",
        "pane_id": "%1",
        "target": "%1",
        "agent": "codex",
        "current_path": "/home/keivenc/project",
        "alternate_on": True,
        "dead": False,
    }]
    second_pane = payload["windows"][1]["panes"][0]
    assert second_pane["dead"] is True
    assert second_pane["dead_status"] == 2
    assert second_pane["dead_time"] == 1710000111
    assert second_pane["in_mode"] is True
    assert second_pane["mode"] == "copy-mode"
    assert second_pane["input_off"] is True
    assert second_pane["synchronized"] is True


def test_parse_tmux_signal_snapshot_reports_bad_rows():
    payload = parse_tmux_signal_snapshot("bad\trow", "also\tbad", generated_at=1, compute_ms=0)

    assert payload["ok"] is False
    assert payload["window_count"] == 0
    assert payload["pane_count"] == 0
    assert payload["errors"] == ["invalid tmux sub-window signal row", "invalid tmux pane signal row"]


def test_parse_pane_signal_row_does_not_promote_an_arbitrary_opencode_command():
    line = "alpha\t0\t@1\t0\t%1\t1\t/home/keivenc/project\topencode\t\t0\t\t\t\t1\t0\t\t0\t0\t1234\t120\t36\t4000\t120000"

    pane = parse_pane_signal_row(line)

    assert pane is not None
    assert pane["current_command"] == "opencode"
    assert pane["agent"] == ""


def test_tmux_control_attach_command_is_readonly_and_ignores_size(monkeypatch):
    monkeypatch.setenv("YOLOMUX_TMUX_SOCKET", "/tmp/yolomux-test.sock")

    command = tmux_control_attach_command("alpha")

    assert command == [
        "tmux",
        "-S",
        "/tmp/yolomux-test.sock",
        "-C",
        "attach-session",
        "-f",
        "read-only,ignore-size",
        "-t",
        "=alpha:",
    ]


def test_control_client_parent_death_signal_requests_sigterm(monkeypatch):
    # The control client must die with the yolomux parent so a hard SIGKILL/crash does not orphan
    # a read-only ignore-size tmux client on the shared socket. The preexec hook asks the kernel
    # for PR_SET_PDEATHSIG=SIGTERM; it must be a no-op (not raise) when libc/prctl is unavailable.
    calls = []

    class FakeLibc:
        def prctl(self, *args):
            calls.append(args)
            return 0

    monkeypatch.setattr(tmux_signals, "_LIBC", FakeLibc())
    tmux_signals.set_control_client_parent_death_signal()
    assert calls == [(tmux_signals._PR_SET_PDEATHSIG, signal.SIGTERM)]

    monkeypatch.setattr(tmux_signals, "_LIBC", None)
    tmux_signals.set_control_client_parent_death_signal()


def test_the_unfenced_ps_scrape_control_client_reaper_stays_deleted():
    """The `ps`-scrape reaper is gone, and must not come back.

    `reap_macos_orphaned_tmux_control_clients` decided to SIGTERM on two facts a hostile or
    merely unlucky process can both present: `PPID == 1`, and the substrings `-C`,
    `attach-session` and `read-only,ignore-size` in its argv. No host id, no boot id, no
    process start identity, no record -- so a recycled pid whose argv happened to match was
    indistinguishable from the real orphan, and the Darwin gate bounded the blast radius
    without making the decision correct. That is the exact authority the queue's Rejected
    Shortcuts forbid: "Do not use hostname, PPID, PGID, command text ... as sufficient
    authority", and "Do not add a broad host sweeper".

    It is replaced by `reap_unsupervised_tmux_control_clients`, which signals only a claim
    that re-proves its recorded birth identity and whose supervisor is provably gone. This
    test asserts the deletion rather than the replacement's behaviour, because the failure
    mode being guarded is a well-meaning reintroduction of the shortcut alongside the
    ledger -- two owners, one of them unfenced.
    """
    assert not hasattr(tmux_signals, "reap_macos_orphaned_tmux_control_clients"), (
        "the unfenced PPID+argv ps-scrape reaper is back; identity-fenced claim reaping is the one owner"
    )
    # Positive control: the replacement really is present, so this cannot pass merely because
    # the module failed to import or the whole reaping concept was dropped.
    assert callable(tmux_signals.reap_unsupervised_tmux_control_clients)
    assert callable(tmux_signals.tmux_control_client_claims)


def test_run_control_client_spawns_with_parent_death_preexec(monkeypatch):
    # run_control_client must spawn the control client with the parent-death preexec hook so the
    # leaked-orphan-on-hard-kill path is closed at the source, not mopped up later.
    captured = {}

    class FakeStdin:
        def write(self, *_):
            pass

        def flush(self):
            pass

    class FakeProcess:
        def __init__(self):
            self.stdin = FakeStdin()
            self.stdout = iter(())  # empty stream -> reader loop exits immediately
            # A real subprocess.Popen always exposes .pid, and the spawn path now publishes a
            # process claim keyed on it. The double omitted it, so it stopped standing in for a
            # real Popen the moment claims were introduced.
            self.pid = 424242

        def poll(self):
            return 0  # already exited -> finally skips terminate/kill

    # Substitute the double ONLY for the control-client spawn this test is about, and let every
    # other subprocess use the real Popen. Spawning now also publishes a process claim, which
    # resolves identity through subprocess.run() -- and run() needs a genuine Popen (context
    # manager, .args, .returncode). Faking every Popen made the double stand in for objects it
    # was never written to be, which surfaced as a chain of unrelated AttributeErrors rather
    # than as a statement about the preexec hook.
    real_popen = tmux_signals.subprocess.Popen

    def fake_popen(command, **kwargs):
        text = " ".join(command) if isinstance(command, (list, tuple)) else str(command)
        if "attach-session" not in text:
            return real_popen(command, **kwargs)
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(tmux_signals.subprocess, "Popen", fake_popen)
    monkeypatch.setenv(tmux_utils.YOLOMUX_TMUX_SOCKET_ENV, "/tmp/control-client.sock")

    watcher = tmux_signals.TmuxSignalEventWatcher(sessions=lambda: ["alpha"], on_event=lambda event: None)
    watcher.run_control_client("alpha")

    assert captured["kwargs"].get("preexec_fn") is tmux_signals.set_control_client_parent_death_signal


def test_stop_terminates_a_spawned_control_client_while_claim_publication_is_pending(monkeypatch):
    claim_started = threading.Event()
    release_claim = threading.Event()
    process_terminated = threading.Event()

    class FakeStdin:
        def write(self, _text):
            return None

        def flush(self):
            return None

    class FakeProcess:
        pid = 424243
        stdin = FakeStdin()
        stdout = iter(())

        def poll(self):
            return 0 if process_terminated.is_set() else None

        def terminate(self):
            process_terminated.set()

        def wait(self, timeout=None):
            assert timeout == 1.0
            return 0

        def kill(self):
            raise AssertionError("terminate completed; kill must not run")

    monkeypatch.setattr(tmux_signals.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    watcher = tmux_signals.TmuxSignalEventWatcher(sessions=lambda: ["alpha"], on_event=lambda _event: None)

    def publish_claim(_pid, _session):
        claim_started.set()
        assert release_claim.wait(timeout=2.0)
        return None

    monkeypatch.setattr(watcher, "publish_client_claim", publish_claim)
    worker = threading.Thread(target=watcher.run_control_client, args=("alpha",))
    worker.start()
    try:
        assert claim_started.wait(timeout=1.0)
        watcher.stop()
        assert process_terminated.is_set()
    finally:
        release_claim.set()
        worker.join(timeout=2.0)
    assert worker.is_alive() is False
    assert watcher.wait_for_status("attached", timeout=0.0) is False

    claim_calls = []
    pre_stopped_watcher = tmux_signals.TmuxSignalEventWatcher(
        sessions=lambda: ["alpha"],
        on_event=lambda _event: None,
    )
    monkeypatch.setattr(pre_stopped_watcher, "publish_client_claim", lambda *_args: claim_calls.append(True))
    monkeypatch.setattr(
        tmux_signals.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("a stopped watcher must not spawn")),
    )
    pre_stopped_watcher.stop()
    pre_stopped_watcher.run_control_client("alpha")
    assert claim_calls == []
    assert pre_stopped_watcher.wait_for_status("attached", timeout=0.0) is False


def test_spawn_publication_is_fenced_from_stop_and_stop_retires_attached_status(monkeypatch):
    spawn_started = threading.Event()
    release_spawn = threading.Event()
    process_terminated = threading.Event()

    class FakeProcess:
        pid = 424244
        stdin = None
        stdout = iter(())

        def poll(self):
            return 0 if process_terminated.is_set() else None

        def terminate(self):
            process_terminated.set()

        def wait(self, timeout=None):
            assert timeout == 1.0
            return 0

        def kill(self):
            raise AssertionError("terminate completed; kill must not run")

    def spawn(*_args, **_kwargs):
        spawn_started.set()
        assert release_spawn.wait(timeout=2.0)
        return FakeProcess()

    monkeypatch.setattr(tmux_signals.subprocess, "Popen", spawn)
    watcher = tmux_signals.TmuxSignalEventWatcher(sessions=lambda: ["alpha"], on_event=lambda _event: None)
    stop_returned = threading.Event()

    def publish_claim(*_args):
        assert stop_returned.wait(timeout=1.0)
        return None

    monkeypatch.setattr(watcher, "publish_client_claim", publish_claim)
    worker = threading.Thread(target=watcher.run_control_client, args=("alpha",))
    worker.start()
    assert spawn_started.wait(timeout=1.0)

    stopper = threading.Thread(target=lambda: (watcher.stop(), stop_returned.set()))
    stopper.start()
    try:
        assert stop_returned.wait(timeout=0.1) is False
    finally:
        release_spawn.set()
        stopper.join(timeout=2.0)
        worker.join(timeout=2.0)

    assert stopper.is_alive() is False
    assert worker.is_alive() is False
    assert stop_returned.is_set()
    assert process_terminated.is_set()
    assert watcher.status_payload()["state"] == "exited"


@pytest.mark.parametrize(
    ("failure", "expected_reason"),
    [
        (tmux_utils.TmuxSocketTargetError("-C attach-session", ["-C", "attach-session"]), "tmux control-mode attach refused"),
        (OSError("tmux executable is unavailable"), "tmux control-mode start failed"),
    ],
)
def test_control_client_pre_spawn_failure_reports_never_started(monkeypatch, failure, expected_reason):
    errors = []
    watcher = tmux_signals.TmuxSignalEventWatcher(
        sessions=lambda: ["alpha"],
        on_event=lambda _event: None,
        on_error=errors.append,
    )

    if isinstance(failure, tmux_utils.TmuxSocketTargetError):
        monkeypatch.setattr(tmux_signals, "tmux_control_attach_command", lambda _session: (_ for _ in ()).throw(failure))
    else:
        monkeypatch.setattr(tmux_signals.subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(failure))

    watcher.run_control_client("alpha")

    payload = watcher.status_payload()
    assert payload["state"] == "never-started"
    assert payload["reason_code"] == "not_started"
    assert expected_reason in payload["reason"]
    assert errors == [payload["reason"]]


def test_tmux_control_event_filter_accepts_signal_notifications():
    assert tmux_control_event_type("%output %1 bytes") == "output"
    assert tmux_control_event_type("yolomux-tmux-signal-hook:pane-exited:%1") == "pane-exited"
    assert tmux_control_event_relevant("%layout-change @1 layout") is True
    assert tmux_control_event_relevant("%subscription-changed activity 1") is True
    assert tmux_control_event_relevant("yolomux-tmux-signal-hook:pane-died:%1") is True
    assert tmux_control_event_relevant("tmux status message") is False
    assert tmux_control_event_relevant("%begin 1 2 3") is False
    assert tmux_control_event_relevant("not a control event") is False


def test_tmux_control_watcher_reinstalls_subscriptions_after_control_client_exit(monkeypatch):
    installs = []
    control_clients = []

    class StopAfterRecovery:
        stopped = False

        def clear(self):
            self.stopped = False

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, _timeout=None):
            return self.stopped

    watcher = tmux_signals.TmuxSignalEventWatcher(sessions=lambda: ["alpha"], on_event=lambda event: None)
    watcher.stop_event = StopAfterRecovery()
    monkeypatch.setattr(tmux_signals, "install_tmux_signal_monitoring", lambda sessions: installs.append(list(sessions)) or [])

    def exited_control_client(session):
        control_clients.append(session)
        if len(control_clients) == 2:
            watcher.stop_event.set()

    monkeypatch.setattr(watcher, "run_control_client", exited_control_client)
    watcher.run()

    assert installs == [["alpha"], ["alpha"]]
    assert control_clients == ["alpha", "alpha"]


def test_tmux_pane_exit_hook_never_writes_into_a_terminal():
    assert tmux_signal_hook_command("pane-exited") == "refresh-client"
    assert tmux_signal_hook_command("pane-died") == "refresh-client"
    assert "display-message" not in tmux_signal_hook_command("pane-exited")


def test_tmux_signal_subscriptions_cover_activity_and_layout_formats():
    commands = tmux_signal_subscription_commands()

    assert ["refresh-client", "-B", "yolomux-window-activity:#{session_name}:#{window_index}:#{window_activity}:#{window_activity_flag}:#{window_bell_flag}:#{window_silence_flag}:#{window_active_clients}"] in commands
    assert ["refresh-client", "-B", "yolomux-window-layout:#{session_name}:#{window_index}:#{window_zoomed_flag}:#{window_layout}:#{window_visible_layout}"] in commands


def test_install_tmux_signal_monitoring_scopes_options_and_hooks(monkeypatch):
    calls = []

    def fake_tmux(args, timeout=0):
        calls.append((args, timeout))
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(tmux_signals, "tmux", fake_tmux)

    errors = install_tmux_signal_monitoring(["alpha", ""], timeout=1.25)

    assert errors == []
    assert (["set-window-option", "-t", "=alpha:", "monitor-activity", "on"], 1.25) in calls
    assert (["set-window-option", "-t", "=alpha:", "monitor-silence", str(tmux_signals.TMUX_SIGNAL_MONITOR_SILENCE_SECONDS)], 1.25) in calls
    hook_calls = [args for args, _timeout in calls if args[:2] == ["set-hook", "-g"]]
    assert len(hook_calls) == len(tmux_signals.TMUX_SIGNAL_HOOKS)
    assert all(f"[{tmux_signals.TMUX_SIGNAL_HOOK_INDEX}]" in args[2] for args in hook_calls)
    assert any("client-resized" in args[2] for args in hook_calls)
    assert any(args[2].startswith("pane-exited") and args[3] == "refresh-client" for args in hook_calls)
    assert any(args[2].startswith("pane-died") and args[3] == "refresh-client" for args in hook_calls)
    assert all(args[3] == tmux_signal_hook_command(args[2].split("[", 1)[0]) for args in hook_calls)
