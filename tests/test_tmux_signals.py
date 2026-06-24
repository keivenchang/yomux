import signal

from yolomux_lib import tmux_signals
from yolomux_lib.tmux_signals import install_tmux_signal_monitoring
from yolomux_lib.tmux_signals import parse_tmux_signal_snapshot
from yolomux_lib.tmux_signals import tmux_signal_subscription_commands
from yolomux_lib.tmux_signals import tmux_control_attach_command
from yolomux_lib.tmux_signals import tmux_control_event_relevant
from yolomux_lib.tmux_signals import tmux_control_event_type
from yolomux_lib.tmux_signals import window_record_key


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
    assert payload["errors"] == ["invalid tmux window signal row", "invalid tmux pane signal row"]


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
        "alpha:",
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

        def poll(self):
            return 0  # already exited -> finally skips terminate/kill

    def fake_popen(command, **kwargs):
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(tmux_signals.subprocess, "Popen", fake_popen)

    watcher = tmux_signals.TmuxSignalEventWatcher(sessions=lambda: ["alpha"], on_event=lambda event: None)
    watcher.run_control_client("alpha")

    assert captured["kwargs"].get("preexec_fn") is tmux_signals.set_control_client_parent_death_signal


def test_tmux_control_event_filter_accepts_signal_notifications():
    assert tmux_control_event_type("%output %1 bytes") == "output"
    assert tmux_control_event_relevant("%layout-change @1 layout") is True
    assert tmux_control_event_relevant("%subscription-changed activity 1") is True
    assert tmux_control_event_relevant("%begin 1 2 3") is False
    assert tmux_control_event_relevant("not a control event") is False


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
    assert (["set-window-option", "-t", "alpha:", "monitor-activity", "on"], 1.25) in calls
    assert (["set-window-option", "-t", "alpha:", "monitor-silence", str(tmux_signals.TMUX_SIGNAL_MONITOR_SILENCE_SECONDS)], 1.25) in calls
    hook_calls = [args for args, _timeout in calls if args[:2] == ["set-hook", "-g"]]
    assert len(hook_calls) == len(tmux_signals.TMUX_SIGNAL_HOOKS)
    assert all(f"[{tmux_signals.TMUX_SIGNAL_HOOK_INDEX}]" in args[2] for args in hook_calls)
    assert any("client-resized" in args[2] for args in hook_calls)
    assert all(args[3] == "refresh-client" for args in hook_calls)
