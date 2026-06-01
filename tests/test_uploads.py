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
