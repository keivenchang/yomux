import json

from yolomux_lib import sessions


def test_find_recent_codex_transcript_matches_session_meta_header(tmp_path):
    root = tmp_path / "codex" / "sessions"
    transcript = root / "2026" / "05" / "rollout-header.jsonl"
    transcript.parent.mkdir(parents=True)
    cwd = "/repo/project"
    lines = [json.dumps({"type": "session_meta", "payload": {"cwd": cwd}})]
    lines.extend(json.dumps({"type": "event", "message": f"line {index}"}) for index in range(400))
    transcript.write_text("\n".join(lines), encoding="utf-8")

    assert sessions.find_recent_codex_transcript(cwd, root=root) == transcript


def test_find_recent_codex_transcript_keeps_tail_fallback(tmp_path):
    root = tmp_path / "codex" / "sessions"
    transcript = root / "2026" / "05" / "rollout-tail.jsonl"
    transcript.parent.mkdir(parents=True)
    cwd = "/repo/project"
    transcript.write_text("\n".join([json.dumps({"type": "session_meta", "payload": {"cwd": "/other"}}), json.dumps({"cwd": cwd})]), encoding="utf-8")

    assert sessions.find_recent_codex_transcript(cwd, root=root) == transcript


def test_codex_transcript_from_process_fd_prefers_open_rollout(tmp_path):
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
