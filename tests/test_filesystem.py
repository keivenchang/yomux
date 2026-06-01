import json
import os
import subprocess
from pathlib import Path

import pytest

from yolomux_lib import filesystem
from yolomux_lib.filesystem import FilesystemError


def test_list_directory_returns_entries(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / ".git").mkdir()
    (tmp_path / "repo" / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (tmp_path / "fixture").mkdir()
    (tmp_path / "fixture" / ".git").mkdir()
    (tmp_path / "file.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "big.dat").write_bytes(b"\x00" * 100)

    payload = filesystem.list_directory(str(tmp_path))

    assert payload["path"] == str(tmp_path)
    assert payload["parent"] == str(tmp_path.parent)
    names = {entry["name"]: entry for entry in payload["entries"]}
    assert names["sub"]["kind"] == "dir"
    assert names["sub"]["is_repo"] is False
    assert names["repo"]["kind"] == "dir"
    assert names["repo"]["is_repo"] is True
    assert names["fixture"]["kind"] == "dir"
    assert names["fixture"]["is_repo"] is False
    assert names["file.txt"]["kind"] == "file"
    assert "is_repo" not in names["file.txt"]
    assert names["file.txt"]["size"] == len("hello")
    assert names["big.dat"]["kind"] == "file"


def test_list_directory_eagerly_returns_git_repo_info(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "checkout", "-b", "feature/repo-row"], cwd=repo, check=True, capture_output=True, text=True)

    payload = filesystem.list_directory(str(tmp_path))

    entries = {entry["name"]: entry for entry in payload["entries"]}
    assert entries["repo"]["is_repo"] is True
    assert entries["repo"]["repo"]["root"] == str(repo)
    assert entries["repo"]["repo"]["name"] == "repo"
    assert entries["repo"]["repo"]["branch"] == "feature/repo-row"


def test_list_directory_rejects_root_outside_default_allowlist():
    with pytest.raises(FilesystemError) as info:
        filesystem.list_directory("/")
    assert info.value.status == 403


def test_filesystem_allowlist_can_include_root(monkeypatch):
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, "/")
    payload = filesystem.list_directory("/")
    assert payload["parent"] is None


def test_filesystem_blocks_secret_paths(monkeypatch, tmp_path):
    home = tmp_path / "home"
    secret = home / ".ssh" / "id_rsa"
    secret.parent.mkdir(parents=True)
    secret.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(home))
    with pytest.raises(FilesystemError) as info:
        filesystem.read_file(str(secret))
    assert info.value.status == 403


def test_delete_path_refuses_configured_root(monkeypatch, tmp_path):
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(tmp_path))
    with pytest.raises(FilesystemError) as info:
        filesystem.delete_path(str(tmp_path))
    assert info.value.status == 403
    assert tmp_path.exists()


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


def test_search_files_returns_fuzzy_matches_and_skips_heavy_dirs_inside_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "hello_x_and_y.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "hello_x_and_y.js").write_text("bad\n", encoding="utf-8")

    payload = filesystem.search_files(str(tmp_path), "xy", 20)

    assert payload["root"] == str(tmp_path)
    paths = [item["relative_path"] for item in payload["files"]]
    assert "src/hello_x_and_y.py" in paths
    assert "node_modules/hello_x_and_y.js" not in paths


def test_search_files_non_repo_root_stays_shallow_but_indexes_child_repos(tmp_path):
    root = tmp_path / "home"
    root.mkdir()
    (root / "top.txt").write_text("top\n", encoding="utf-8")
    (root / "notes").mkdir()
    (root / "notes" / "nested.md").write_text("too deep\n", encoding="utf-8")
    (root / ".cache").mkdir()
    (root / ".cache" / "cache.txt").write_text("skip\n", encoding="utf-8")
    repo = root / "project"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "src").mkdir()
    (repo / "src" / "deep.py").write_text("print('repo')\n", encoding="utf-8")

    payload = filesystem.search_files(str(root), "", 50)

    paths = {item["relative_path"] for item in payload["files"]}
    assert "top.txt" in paths
    assert "project/src/deep.py" in paths
    assert "notes/nested.md" not in paths
    assert ".cache/cache.txt" not in paths
    assert "project/.git/HEAD" not in paths


def test_search_files_matches_absolute_path_segments(tmp_path):
    project = tmp_path / "home" / "keivenc" / "project"
    project.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True, text=True)
    (project / "README.md").write_text("# ok\n", encoding="utf-8")

    payload = filesystem.search_files(str(project), "hokread", 20)

    assert [item["relative_path"] for item in payload["files"]] == ["README.md"]


def test_search_files_marks_generated_upload_names(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    upload = tmp_path / "20260531-001-diagram.png"
    normal = tmp_path / "diagram.png"
    upload.write_bytes(b"png")
    normal.write_bytes(b"png")

    payload = filesystem.search_files(str(tmp_path), "diagram", 20)
    by_name = {item["name"]: item for item in payload["files"]}

    assert by_name["20260531-001-diagram.png"]["uploaded"] is True
    assert by_name["diagram.png"]["uploaded"] is False


def test_search_files_rejects_non_directory(tmp_path):
    target = tmp_path / "note.md"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(FilesystemError) as info:
        filesystem.search_files(str(target), "x")
    assert info.value.status == 400


def test_read_file_returns_text(tmp_path):
    file_path = tmp_path / "note.md"
    file_path.write_text("# hello\n", encoding="utf-8")
    payload = filesystem.read_file(str(file_path))
    assert payload["content"] == "# hello\n"
    assert payload["extension"] == ".md"
    assert payload["is_text_extension"] is True
    assert payload["size"] == file_path.stat().st_size
    assert payload["mtime_ns"] == file_path.stat().st_mtime_ns


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
    assert result["mtime_ns"] == target.stat().st_mtime_ns

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


def test_write_file_mtime_conflict_uses_nanoseconds(tmp_path):
    target = tmp_path / "race-ns.txt"
    target.write_text("a", encoding="utf-8")
    base_ns = 1_800_000_000_123_456_000
    os.utime(target, ns=(base_ns, base_ns))
    os.utime(target, ns=(base_ns + 1, base_ns + 1))

    with pytest.raises(FilesystemError) as info:
        filesystem.write_file(str(target), "b", expected_mtime=base_ns)

    assert info.value.status == 409


def test_write_file_accepts_legacy_second_mtime(tmp_path):
    target = tmp_path / "race-legacy.txt"
    target.write_text("a", encoding="utf-8")
    legacy_mtime = int(target.stat().st_mtime)

    result = filesystem.write_file(str(target), "b", expected_mtime=legacy_mtime)

    assert result["size"] == 1


def test_rename_path_same_directory(tmp_path):
    target = tmp_path / "old.txt"
    target.write_text("hello", encoding="utf-8")

    result = filesystem.rename_path(str(target), "new.txt")

    assert result["old_path"] == str(target)
    assert result["path"] == str(tmp_path / "new.txt")
    assert not target.exists()
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "hello"


def test_rename_path_rejects_nested_name(tmp_path):
    target = tmp_path / "old.txt"
    target.write_text("hello", encoding="utf-8")

    with pytest.raises(FilesystemError) as info:
        filesystem.rename_path(str(target), "nested/new.txt")

    assert info.value.status == 400


def test_delete_path_removes_directory_tree(tmp_path):
    target = tmp_path / "dir"
    (target / "nested").mkdir(parents=True)
    (target / "nested" / "file.txt").write_text("hello", encoding="utf-8")

    result = filesystem.delete_path(str(target))

    assert result["deleted"] is True
    assert result["kind"] == "dir"
    assert not target.exists()


def test_path_info_returns_git_relative_path(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_text("print('hi')\n", encoding="utf-8")

    result = filesystem.path_info(str(target))

    assert result["repo_root"] == str(tmp_path)
    assert result["relative_path"] == "src/main.py"
    assert result["kind"] == "file"


def test_diff_file_returns_git_diff_for_tracked_file(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True, capture_output=True, text=True)
    target = tmp_path / "app.py"
    target.write_text("print('one')\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True, capture_output=True, text=True)
    target.write_text("print('two')\n", encoding="utf-8")

    result = filesystem.diff_file(str(target))

    assert result["repo"] == str(tmp_path)
    assert result["relative_path"] == "app.py"
    assert result["untracked"] is False
    assert result["original"] == "print('one')\n"
    assert result["working_missing"] is False
    assert "-print('one')" in result["diff"]
    assert "+print('two')" in result["diff"]


def test_diff_file_returns_no_index_diff_for_untracked_file(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    target = tmp_path / "new.txt"
    target.write_text("hello\n", encoding="utf-8")

    result = filesystem.diff_file(str(target))

    assert result["relative_path"] == "new.txt"
    assert result["untracked"] is True
    assert result["original"] == ""
    assert "+hello" in result["diff"]


def test_diff_file_returns_head_content_for_deleted_file(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True, capture_output=True, text=True)
    target = tmp_path / "gone.txt"
    target.write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "add", "gone.txt"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True, capture_output=True, text=True)
    target.unlink()

    result = filesystem.diff_file(str(target))

    assert result["original"] == "old\n"
    assert result["working_missing"] is True
    assert "-old" in result["diff"]


def test_diff_file_supports_commit_to_commit_refs(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True, capture_output=True, text=True)
    target = tmp_path / "app.py"
    target.write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "one"], cwd=tmp_path, check=True, capture_output=True, text=True)
    older = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()
    target.write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "two"], cwd=tmp_path, check=True, capture_output=True, text=True)
    newer = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()

    result = filesystem.diff_file(str(target), from_ref=newer, to_ref=older)

    assert result["from_ref"] == newer
    assert result["to_ref"] == older
    assert result["original"] == "one\n"
    assert result["working"] == "two\n"
    assert "-one" in result["diff"]
    assert "+two" in result["diff"]


def test_diff_file_rejects_to_ref_newer_than_from_ref(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True, capture_output=True, text=True)
    target = tmp_path / "app.py"
    target.write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "one"], cwd=tmp_path, check=True, capture_output=True, text=True)
    older = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()
    target.write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "two"], cwd=tmp_path, check=True, capture_output=True, text=True)
    newer = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()

    with pytest.raises(filesystem.FilesystemError) as excinfo:
        filesystem.diff_file(str(target), from_ref=older, to_ref=newer)

    assert excinfo.value.status == 400
    assert "TO ref must be older" in str(excinfo.value)


def test_create_directory_rejects_existing_target(tmp_path):
    created = filesystem.create_directory(str(tmp_path / "new-dir"))

    assert created["kind"] == "dir"
    assert (tmp_path / "new-dir").is_dir()
    with pytest.raises(filesystem.FilesystemError) as excinfo:
        filesystem.create_directory(str(tmp_path / "new-dir"))
    assert excinfo.value.status == 409


def test_is_text_path_recognizes_known_extensions():
    for extension in filesystem.TEXT_EXTENSIONS:
        assert filesystem.is_text_path(f"/tmp/foo{extension}")
    assert filesystem.is_text_path("/tmp/.gitignore")
    assert filesystem.is_text_path("/tmp/.dockerignore")
    assert filesystem.is_text_path("/tmp/.dockerfile")
    assert filesystem.is_text_path("/tmp/Dockerfile")
    assert filesystem.is_text_path("/tmp/Makefile")
    assert filesystem.is_text_path("/tmp/LICENSE")
    assert filesystem.is_text_path("/tmp/README")
    assert filesystem.is_text_path("/tmp/foo.PY")
    assert not filesystem.is_text_path("/tmp/foo.png")
    assert not filesystem.is_text_path("/tmp/foo.PNG")
    assert not filesystem.is_text_path("/tmp/foo.exe")
