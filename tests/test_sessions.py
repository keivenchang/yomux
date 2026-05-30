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
