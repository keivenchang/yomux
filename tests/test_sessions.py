import json
from pathlib import Path

from yolomux_lib import sessions
from yolomux_lib.common import PaneInfo, ProcessInfo
from yolomux_lib.sessions import pane_process_label


def clear_transcript_lookup_cache():
    # the lookup cache is now a shared TtlCache (was a hand-rolled dict + lock).
    sessions._TRANSCRIPT_LOOKUP_CACHE.clear()


def test_find_recent_codex_transcript_matches_session_meta_header(tmp_path):
    clear_transcript_lookup_cache()
    root = tmp_path / "codex" / "sessions"
    transcript = root / "2026" / "05" / "rollout-header.jsonl"
    transcript.parent.mkdir(parents=True)
    cwd = "/repo/project"
    lines = [json.dumps({"type": "session_meta", "payload": {"cwd": cwd}})]
    lines.extend(json.dumps({"type": "event", "message": f"line {index}"}) for index in range(400))
    transcript.write_text("\n".join(lines), encoding="utf-8")

    assert sessions.find_recent_codex_transcript(cwd, root=root) == transcript


def test_find_recent_codex_transcript_keeps_tail_fallback(tmp_path):
    clear_transcript_lookup_cache()
    root = tmp_path / "codex" / "sessions"
    transcript = root / "2026" / "05" / "rollout-tail.jsonl"
    transcript.parent.mkdir(parents=True)
    cwd = "/repo/project"
    transcript.write_text("\n".join([json.dumps({"type": "session_meta", "payload": {"cwd": "/other"}}), json.dumps({"cwd": cwd})]), encoding="utf-8")

    assert sessions.find_recent_codex_transcript(cwd, root=root) == transcript


def test_find_recent_codex_transcript_refuses_global_newest_without_cwd(tmp_path):
    clear_transcript_lookup_cache()
    root = tmp_path / "codex" / "sessions"
    transcript = root / "2026" / "05" / "rollout-newest.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": "/repo/other"}}), encoding="utf-8")

    assert sessions.find_recent_codex_transcript(None, root=root) is None
    assert sessions.find_recent_codex_transcript("", root=root) is None
    assert sessions.find_recent_codex_transcript("/repo/project", root=root) is None


def test_find_recent_codex_transcript_caches_cwd_lookup(tmp_path, monkeypatch):
    clear_transcript_lookup_cache()
    root = tmp_path / "codex" / "sessions"
    transcript = root / "2026" / "05" / "rollout-header.jsonl"
    transcript.parent.mkdir(parents=True)
    cwd = "/repo/project"
    transcript.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": cwd}}), encoding="utf-8")
    calls = []
    original_header = sessions.codex_transcript_header_cwd
    monkeypatch.setattr(sessions, "codex_transcript_header_cwd", lambda path: calls.append(path) or original_header(path))

    assert sessions.find_recent_codex_transcript(cwd, root=root) == transcript
    assert sessions.find_recent_codex_transcript(cwd, root=root) == transcript

    assert calls == [transcript]


def test_find_recent_codex_transcript_orders_by_name_without_stat_storm(tmp_path, monkeypatch):
    clear_transcript_lookup_cache()
    root = tmp_path / "codex" / "sessions"
    day = root / "2026" / "06" / "01"
    day.mkdir(parents=True)
    cwd = "/repo/project"
    older = day / "rollout-2026-06-01T08-00-00-aaaa.jsonl"
    newer = day / "rollout-2026-06-01T10-00-00-bbbb.jsonl"
    for path in (older, newer):
        path.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": cwd}}), encoding="utf-8")

    stat_calls: list[Path] = []
    original_stat = Path.stat
    monkeypatch.setattr(Path, "stat", lambda self, *args, **kwargs: (stat_calls.append(self) or original_stat(self, *args, **kwargs)))

    # Newest-by-name wins, and ordering issues no per-rollout-file stat() (no syscall storm).
    assert sessions.find_recent_codex_transcript(cwd, root=root) == newer
    assert [path for path in stat_calls if path.name.startswith("rollout-")] == []


def test_find_transcript_by_session_id_caches_glob(tmp_path, monkeypatch):
    clear_transcript_lookup_cache()
    root = tmp_path / "claude" / "projects"
    transcript = root / "repo" / "session-123.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n", encoding="utf-8")
    glob_calls = []
    original_glob = Path.glob

    def counted_glob(self, pattern):
        if self == root:
            glob_calls.append(pattern)
        return original_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", counted_glob)

    assert sessions.find_transcript_by_session_id(root, "session-123") == transcript
    assert sessions.find_transcript_by_session_id(root, "session-123") == transcript

    assert glob_calls == ["**/session-123.jsonl"]


def test_codex_transcript_from_process_fd_prefers_open_rollout(tmp_path):
    clear_transcript_lookup_cache()
    root = tmp_path / "codex" / "sessions"
    transcript = root / "2026" / "05" / "rollout-owned.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n", encoding="utf-8")
    outside = tmp_path / "outside" / "rollout-other.jsonl"
    outside.parent.mkdir()
    outside.write_text("{}\n", encoding="utf-8")
    fd_dir = tmp_path / "fd"
    fd_dir.mkdir()
    (fd_dir / "3").symlink_to(outside)
    (fd_dir / "4").symlink_to(transcript)
    (fd_dir / "5").symlink_to(root / "2026" / "05" / "notes.txt")

    assert sessions.codex_transcript_from_process_fd(123, root=root, fd_dir=fd_dir) == transcript


def test_codex_transcript_from_process_fd_accepts_deleted_suffix(tmp_path, monkeypatch):
    clear_transcript_lookup_cache()
    root = tmp_path / "codex" / "sessions"
    transcript = root / "2026" / "05" / "rollout-owned.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n", encoding="utf-8")
    fd_dir = tmp_path / "fd"
    fd_dir.mkdir()
    entry = fd_dir / "3"
    entry.write_text("", encoding="utf-8")

    def fake_readlink(path):
        assert path == entry
        return f"{transcript} (deleted)"

    monkeypatch.setattr(sessions.os, "readlink", fake_readlink)

    assert sessions.codex_transcript_from_process_fd(123, root=root, fd_dir=fd_dir) == transcript


def _pane(pid=100):
    return PaneInfo(
        session="1",
        window="0",
        pane="0",
        pane_id="%1",
        target="1:0.0",
        current_path="/repo",
        command="bash",
        active=True,
        window_active=True,
        title="",
        pid=pid,
    )


def test_active_window_for_panes_supports_typed_and_cached_payloads():
    inactive = _pane(100)
    inactive = PaneInfo(**{**inactive.__dict__, "window": "0", "window_active": False})
    active = _pane(101)
    active = PaneInfo(**{**active.__dict__, "window": "2", "window_active": True})
    assert sessions.active_window_for_panes([inactive, active]) == "2"
    assert sessions.active_window_for_panes([
        {"window": "0", "window_active": False},
        {"window": "3", "window_active": True},
    ]) == "3"


def test_pane_process_label_returns_displayed_process_pid():
    label, pid = pane_process_label(
        _pane(100),
        [
            ProcessInfo(pid=100, ppid=1, command="bash"),
            ProcessInfo(pid=321, ppid=100, command="python3 mock_server.py"),
        ],
    )

    assert label == "mock_server.py"
    assert pid == 321


def test_pane_process_label_falls_back_to_pane_pid_for_shell():
    label, pid = pane_process_label(_pane(100), [ProcessInfo(pid=100, ppid=1, command="bash")])

    assert label == "bash"
    assert pid == 100
