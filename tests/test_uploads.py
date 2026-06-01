import os
from pathlib import Path

os.environ.setdefault("YOLOMUX_CONFIG_DIR", "/tmp/yolomux-test-config")

from yolomux_lib.common import DEFAULT_UPLOAD_FILENAME_TEMPLATE
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


def test_upload_default_size_cap_is_low_enough_for_buffered_parser():
    source = Path("yolomux_lib/common.py").read_text(encoding="utf-8")

    assert 'UPLOAD_MAX_BYTES = positive_env_int("YOLOMUX_UPLOAD_MAX_BYTES", 20 * 1024 * 1024)' in source


def test_upload_request_limit_comes_from_live_settings():
    source = Path("yolomux_lib/server.py").read_text(encoding="utf-8")

    assert "self.server.app.upload_max_bytes()" in source
    assert "content_length > UPLOAD_MAX_BYTES" not in source
