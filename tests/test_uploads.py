import inspect
import os
import stat
import time

import pytest


from yolomux_lib.common import DEFAULT_UPLOAD_FILENAME_TEMPLATE
from yolomux_lib.common import UPLOAD_MAX_BYTES
from yolomux_lib.common import UPLOAD_MAX_FILES
from yolomux_lib.server import Handler
from yolomux_lib.uploads import UploadTargetError
from yolomux_lib.uploads import UploadRetentionSweeper
from yolomux_lib.uploads import central_upload_target
from yolomux_lib.uploads import parse_multipart_upload
from yolomux_lib.uploads import prune_expired_uploads
from yolomux_lib.uploads import upload_path_component
from yolomux_lib.uploads import unique_upload_path


def test_unique_upload_path_uses_template_with_original_name(tmp_path):
    path = unique_upload_path(tmp_path, "diagram.png", DEFAULT_UPLOAD_FILENAME_TEMPLATE)

    assert path.name.endswith("-diagram.png")
    assert path.name[:8].isdigit()
    assert path.name[8] == "-"


def test_unique_upload_path_omits_name_segment_for_paste(tmp_path):
    path = unique_upload_path(tmp_path, "20260531-001.png", DEFAULT_UPLOAD_FILENAME_TEMPLATE)

    assert path.name == "20260531-001.png"


def test_unique_upload_path_increments_template_sequence(tmp_path):
    (tmp_path / "20260531-001.png").write_bytes(b"x")

    path = unique_upload_path(tmp_path, "20260531-001.png", DEFAULT_UPLOAD_FILENAME_TEMPLATE)

    assert path.name == "20260531-002.png"


def test_central_upload_target_is_private_sanitized_and_disjoint_per_auth_user(tmp_path):
    alice, alice_root = central_upload_target("alice", "project/one", tmp_base=tmp_path)
    bob, bob_root = central_upload_target("bob", "project/one", tmp_base=tmp_path)

    assert alice == alice_root / "uploads" / upload_path_component("project/one", "session")
    assert bob == bob_root / "uploads" / upload_path_component("project/one", "session")
    assert alice_root != bob_root
    assert alice_root.name == "yolomux.alice"
    assert bob_root.name == "yolomux.bob"
    for directory in (alice_root, alice_root / "uploads", alice, bob_root, bob_root / "uploads", bob):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_central_upload_target_defaults_empty_auth_and_refuses_squatters(monkeypatch, tmp_path):
    default_target, default_root = central_upload_target("", "s", tmp_base=tmp_path)
    assert default_target == tmp_path / "yolomux.default" / "uploads" / "s"
    assert default_root.name == "yolomux.default"

    outside = tmp_path / "outside"
    outside.mkdir()
    squatted = tmp_path / "yolomux.mallory"
    squatted.symlink_to(outside, target_is_directory=True)
    with pytest.raises(UploadTargetError, match="symlink"):
        central_upload_target("mallory", "s", tmp_base=tmp_path)

    owned = tmp_path / "yolomux.owner"
    owned.mkdir()
    monkeypatch.setattr(os, "geteuid", lambda: owned.stat().st_uid + 1)
    with pytest.raises(UploadTargetError, match="owned by uid"):
        central_upload_target("owner", "s", tmp_base=tmp_path)


def test_upload_retention_prunes_only_expired_regular_files_in_verified_tree(tmp_path):
    target, user_root = central_upload_target("alice", "s", tmp_base=tmp_path)
    expired = target / "expired.png"
    fresh = target / "fresh.png"
    legacy = tmp_path / "repo" / ".uploads" / "legacy.png"
    legacy.parent.mkdir(parents=True)
    expired.write_bytes(b"old")
    fresh.write_bytes(b"new")
    legacy.write_bytes(b"legacy")
    now = time.time()
    os.utime(expired, (now - (10 * 86400), now - (10 * 86400)))
    os.utime(legacy, (now - (10 * 86400), now - (10 * 86400)))

    result = prune_expired_uploads(user_root, 7, now=now)

    assert result["removed"] == 1
    assert not expired.exists()
    assert fresh.read_bytes() == b"new"
    assert legacy.read_bytes() == b"legacy"


def test_upload_retention_rechecks_immediately_when_setting_changes(tmp_path):
    target, user_root = central_upload_target("alice", "s", tmp_base=tmp_path)
    candidate = target / "five-days-old.png"
    candidate.write_bytes(b"old")
    now = time.time()
    os.utime(candidate, (now - (5 * 86400), now - (5 * 86400)))
    sweeper = UploadRetentionSweeper(clock=lambda: 10.0)

    assert sweeper.maybe_prune(user_root, 7)["removed"] == 0
    assert sweeper.maybe_prune(user_root, 7) == {"scanned": 0, "removed": 0}
    assert sweeper.maybe_prune(user_root, 1)["removed"] == 1
    assert not candidate.exists()


def test_upload_default_size_cap_matches_file_transfer_preference_default():
    assert UPLOAD_MAX_BYTES == 300 * 1024 * 1024


def test_upload_request_limit_comes_from_live_settings():
    # Scoped to handle_upload (inspect, not full-file string slicing): the per-request cap comes from
    # the LIVE settings (app.file_transfer_max_bytes()), not the static module constant, and feeds the parser.
    body = inspect.getsource(Handler.handle_upload)

    assert "self.file_transfer_max_bytes()" in body
    assert "content_length > UPLOAD_MAX_BYTES" not in body
    assert 'parse_multipart_upload(self.headers.get("Content-Type", ""), body or b"", max_part_bytes=upload_max_bytes)' in body


def multipart_body(parts, boundary="test-boundary"):
    chunks = []
    for headers, content in parts:
        header_text = "\r\n".join(headers)
        chunks.append(f"--{boundary}\r\n{header_text}\r\n\r\n".encode("utf-8") + content + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return f"multipart/form-data; boundary={boundary}", b"".join(chunks)


def test_parse_multipart_upload_extracts_files_and_filename_star():
    content_type, body = multipart_body([
        (['Content-Disposition: form-data; name="file"; filename="one.txt"'], b"one"),
        (["Content-Disposition: form-data; name=\"file\"; filename*=utf-8''two%20words.txt"], b"two"),
    ])

    files = parse_multipart_upload(content_type, body)

    assert [file.filename for file in files] == ["one.txt", "two words.txt"]
    assert [file.content for file in files] == [b"one", b"two"]


def test_parse_multipart_upload_rejects_bad_boundary_and_limits():
    try:
        parse_multipart_upload("multipart/form-data", b"")
    except ValueError as exc:
        assert "missing multipart boundary" in str(exc)
    else:
        raise AssertionError("missing multipart boundary should fail")

    content_type, body = multipart_body([
        ([f'Content-Disposition: form-data; name="file"; filename="{index}.txt"'], b"x")
        for index in range(UPLOAD_MAX_FILES + 1)
    ])
    try:
        parse_multipart_upload(content_type, body)
    except ValueError as exc:
        assert f"too many files; limit is {UPLOAD_MAX_FILES}" in str(exc)
    else:
        raise AssertionError("too many multipart files should fail")

    content_type, body = multipart_body([
        (['Content-Disposition: form-data; name="file"; filename="large.txt"'], b"too large"),
    ])
    try:
        parse_multipart_upload(content_type, body, max_part_bytes=3)
    except ValueError as exc:
        assert "file is too large; limit is 3 bytes" in str(exc)
    else:
        raise AssertionError("oversized multipart part should fail")
