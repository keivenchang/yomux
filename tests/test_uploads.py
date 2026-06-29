import inspect
import os


from yolomux_lib.common import DEFAULT_UPLOAD_FILENAME_TEMPLATE
from yolomux_lib.common import UPLOAD_MAX_BYTES
from yolomux_lib.common import UPLOAD_MAX_FILES
from yolomux_lib.server import Handler
from yolomux_lib.uploads import parse_multipart_upload
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
