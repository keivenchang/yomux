import json
from pathlib import Path

import pytest

from yolomux_lib import filesystem
from yolomux_lib.filesystem import FilesystemError


def test_list_directory_returns_entries(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "file.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "big.dat").write_bytes(b"\x00" * 100)

    payload = filesystem.list_directory(str(tmp_path))

    assert payload["path"] == str(tmp_path)
    assert payload["parent"] == str(tmp_path.parent)
    names = {entry["name"]: entry for entry in payload["entries"]}
    assert names["sub"]["kind"] == "dir"
    assert names["file.txt"]["kind"] == "file"
    assert names["file.txt"]["size"] == len("hello")
    assert names["big.dat"]["kind"] == "file"


def test_list_directory_root_has_no_parent():
    payload = filesystem.list_directory("/")
    assert payload["parent"] is None


def test_list_directory_rejects_relative():
    with pytest.raises(FilesystemError) as info:
        filesystem.list_directory("relative/path")
    assert info.value.status == 400


def test_list_directory_rejects_crlf_and_nul():
    for bad in ("/etc/hosts\n", "/etc\x00", "/etc/hosts\r"):
        with pytest.raises(FilesystemError):
            filesystem.list_directory(bad)


def test_list_directory_missing(tmp_path):
    with pytest.raises(FilesystemError) as info:
        filesystem.list_directory(str(tmp_path / "does-not-exist"))
    assert info.value.status == 404


def test_list_directory_not_a_dir(tmp_path):
    file_path = tmp_path / "f.txt"
    file_path.write_text("x")
    with pytest.raises(FilesystemError) as info:
        filesystem.list_directory(str(file_path))
    assert info.value.status == 400


def test_read_file_returns_text(tmp_path):
    file_path = tmp_path / "note.md"
    file_path.write_text("# hello\n", encoding="utf-8")
    payload = filesystem.read_file(str(file_path))
    assert payload["content"] == "# hello\n"
    assert payload["extension"] == ".md"
    assert payload["is_text_extension"] is True
    assert payload["size"] == file_path.stat().st_size


def test_read_file_rejects_binary(tmp_path):
    file_path = tmp_path / "binary.bin"
    file_path.write_bytes(b"abc\x00def")
    with pytest.raises(FilesystemError) as info:
        filesystem.read_file(str(file_path))
    assert info.value.status == 415


def test_read_file_too_large(tmp_path, monkeypatch):
    file_path = tmp_path / "big.txt"
    file_path.write_text("x" * 100, encoding="utf-8")
    monkeypatch.setattr(filesystem, "MAX_READ_BYTES", 10)
    with pytest.raises(FilesystemError) as info:
        filesystem.read_file(str(file_path))
    assert info.value.status == 413


def test_read_file_missing(tmp_path):
    with pytest.raises(FilesystemError) as info:
        filesystem.read_file(str(tmp_path / "no.txt"))
    assert info.value.status == 404


def test_write_file_creates_and_overwrites(tmp_path):
    target = tmp_path / "out.json"
    result = filesystem.write_file(str(target), '{"a": 1}\n')
    assert target.read_text(encoding="utf-8") == '{"a": 1}\n'
    assert result["size"] == len('{"a": 1}\n')

    second = filesystem.write_file(str(target), 'replaced')
    assert target.read_text(encoding="utf-8") == 'replaced'
    assert second["mtime"] >= result["mtime"]


def test_write_file_rejects_directory(tmp_path):
    with pytest.raises(FilesystemError) as info:
        filesystem.write_file(str(tmp_path), "data")
    assert info.value.status == 400


def test_write_file_creates_parents(tmp_path):
    target = tmp_path / "nested" / "deep" / "file.txt"
    filesystem.write_file(str(target), "ok")
    assert target.read_text(encoding="utf-8") == "ok"


def test_write_file_too_large(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem, "MAX_WRITE_BYTES", 5)
    with pytest.raises(FilesystemError) as info:
        filesystem.write_file(str(tmp_path / "x.txt"), "too-long")
    assert info.value.status == 413


def test_write_file_mtime_conflict(tmp_path):
    target = tmp_path / "race.txt"
    target.write_text("a", encoding="utf-8")
    stale_mtime = int(target.stat().st_mtime) - 100  # pretend the client saw an older version
    with pytest.raises(FilesystemError) as info:
        filesystem.write_file(str(target), "b", expected_mtime=stale_mtime)
    assert info.value.status == 409


def test_is_text_path_recognizes_known_extensions():
    assert filesystem.is_text_path("/tmp/foo.py")
    assert filesystem.is_text_path("/tmp/foo.json")
    assert filesystem.is_text_path("/tmp/foo.md")
    assert not filesystem.is_text_path("/tmp/foo.png")
    assert not filesystem.is_text_path("/tmp/foo.exe")
