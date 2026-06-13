import json
import os
import subprocess
from pathlib import Path

import pytest

from yolomux_lib import filesystem
from yolomux_lib.filesystem import FilesystemError

from _git_helpers import git, init_repo


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
    git(repo, "init")
    git(repo, "checkout", "-b", "feature/repo-row")

    payload = filesystem.list_directory(str(tmp_path))

    entries = {entry["name"]: entry for entry in payload["entries"]}
    assert entries["repo"]["is_repo"] is True
    assert entries["repo"]["repo"]["root"] == str(repo)
    assert entries["repo"]["repo"]["name"] == "repo"
    assert entries["repo"]["repo"]["branch"] == "feature/repo-row"


def test_list_directory_allows_root_by_default(monkeypatch):
    monkeypatch.delenv(filesystem.FS_ROOTS_ENV, raising=False)
    payload = filesystem.list_directory("/")
    assert payload["path"] == "/"
    assert payload["parent"] is None


def test_filesystem_allowlist_env_can_narrow_scope(monkeypatch, tmp_path):
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(tmp_path))
    with pytest.raises(FilesystemError) as info:
        filesystem.list_directory("/")
    assert info.value.status == 403


def test_filesystem_blocks_home_secret_paths(monkeypatch, tmp_path):
    home = tmp_path / "home"
    secret = home / ".ssh" / "id_rsa"
    secret.parent.mkdir(parents=True)
    secret.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(home))
    with pytest.raises(FilesystemError) as info:
        filesystem.read_file(str(secret))
    assert info.value.status == 403


def test_list_directory_hides_secret_entries(tmp_path):
    visible = tmp_path / "visible.txt"
    visible.write_text("ok", encoding="utf-8")
    for path in (
        tmp_path / ".ssh" / "id_rsa",
        tmp_path / ".config" / "gh" / "hosts.yml",
        tmp_path / ".config" / "git" / "config",
        tmp_path / ".config" / "gitlab-token",
        tmp_path / ".cache" / "huggingface" / "token",
        tmp_path / ".docker" / "config.json",
        tmp_path / ".ngc" / "config",
        tmp_path / ".netrc",
        tmp_path / ".npmrc",
        tmp_path / ".pypirc",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("secret\n", encoding="utf-8")

    names = {entry["name"] for entry in filesystem.list_directory(str(tmp_path))["entries"]}

    assert "visible.txt" in names
    assert ".ssh" not in names
    assert ".netrc" not in names
    assert ".npmrc" not in names
    assert ".pypirc" not in names
    config_names = {entry["name"] for entry in filesystem.list_directory(str(tmp_path / ".config"))["entries"]}
    assert "gh" not in config_names
    assert "git" not in config_names
    assert "gitlab-token" not in config_names
    docker_names = {entry["name"] for entry in filesystem.list_directory(str(tmp_path / ".docker"))["entries"]}
    ngc_names = {entry["name"] for entry in filesystem.list_directory(str(tmp_path / ".ngc"))["entries"]}
    assert "config.json" not in docker_names
    assert "config" not in ngc_names


def test_filesystem_blocks_symlink_escape_from_allowed_root(monkeypatch, tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    target = outside / "secret.txt"
    target.write_text("secret\n", encoding="utf-8")
    link = allowed / "link.txt"
    link.symlink_to(target)
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(allowed))

    with pytest.raises(FilesystemError) as info:
        filesystem.read_file(str(link))

    assert info.value.status == 403
    listed_link = {entry["name"]: entry for entry in filesystem.list_directory(str(allowed))["entries"]}["link.txt"]
    assert "file_id" not in listed_link
    assert "file_identity" not in listed_link
    assert "realpath" not in listed_link


def test_filesystem_blocks_exact_secret_files(monkeypatch, tmp_path):
    home = tmp_path / "home"
    config_dir = home / ".config" / "yolomux"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(filesystem, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(filesystem, "AUTH_CONFIG_PATH", config_dir / "auth.yaml")
    monkeypatch.setattr(filesystem, "AUTH_COOKIE_SECRET_PATH", config_dir / "auth-cookie-secret")
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(home))
    secret_paths = [
        config_dir / "auth.yaml",
        config_dir / "auth-cookie-secret",
        home / ".config" / "gitlab-token",
        home / ".cache" / "huggingface" / "token",
        home / ".docker" / "config.json",
        home / ".ngc" / "config",
    ]
    for secret in secret_paths:
        secret.parent.mkdir(parents=True, exist_ok=True)
        secret.write_text("secret\n", encoding="utf-8")

    for secret in secret_paths:
        with pytest.raises(FilesystemError) as info:
            filesystem.read_file(str(secret))
        assert info.value.status == 403


def test_filesystem_blocks_secret_patterns_outside_home(tmp_path):
    secret_paths = [
        tmp_path / ".ssh" / "id_rsa",
        tmp_path / ".gnupg" / "private-keys-v1.d" / "key",
        tmp_path / ".aws" / "credentials",
        tmp_path / ".azure" / "accessTokens.json",
        tmp_path / ".kube" / "config",
        tmp_path / ".config" / "gh" / "hosts.yml",
        tmp_path / ".config" / "git" / "credentials",
        tmp_path / ".config" / "gitlab-token",
        tmp_path / ".cache" / "huggingface" / "token",
        tmp_path / ".docker" / "config.json",
        tmp_path / ".ngc" / "config",
        tmp_path / ".netrc",
        tmp_path / ".npmrc",
        tmp_path / ".pypirc",
    ]
    for path in secret_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("secret\n", encoding="utf-8")

    for path in secret_paths:
        with pytest.raises(FilesystemError) as info:
            filesystem.read_file(str(path))
        assert info.value.status == 403


def test_filesystem_allows_non_secret_docker_like_directories(tmp_path):
    compose = tmp_path / ".docker" / "compose.yaml"
    compose.parent.mkdir()
    compose.write_text("services: {}\n", encoding="utf-8")

    payload = filesystem.read_file(str(compose))

    assert payload["content"] == "services: {}\n"


def test_search_files_skips_secret_paths(tmp_path):
    visible = tmp_path / "visible-target.txt"
    visible.write_text("target\n", encoding="utf-8")
    secrets = [
        tmp_path / ".ssh" / "secret-target.txt",
        tmp_path / ".config" / "gh" / "secret-target.txt",
        tmp_path / ".config" / "gitlab-token",
        tmp_path / ".docker" / "config.json",
        tmp_path / ".ngc" / "config",
        tmp_path / ".netrc",
    ]
    for path in secrets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("target\n", encoding="utf-8")

    payload = filesystem.search_files(str(tmp_path), "target", 50, recursive=True)
    paths = {item["path"] for item in payload["files"]}

    assert str(visible) in paths
    assert all(str(path) not in paths for path in secrets)


def test_read_raw_streams_preview_media_with_mime_type(tmp_path):
    cases = [
        ("tiny.png", b"\x89PNG\r\n\x1a\n", "image/png"),
        ("photo.avif", b"\x00\x00\x00 ftypavif", "image/avif"),
        ("spec.pdf", b"%PDF-1.7\n", "application/pdf"),
        ("spec", b"%PDF-1.7\n", "application/pdf"),
        ("renamed.bin", b"\x89PNG\r\n\x1a\n", "image/png"),
        ("photo.tiff", b"II*\x00rest", "image/tiff"),
        ("photo.heic", b"\x00\x00\x00 ftypheic", "image/heic"),
        ("sound.mp3", b"ID3\x03\x00\x00", "audio/mpeg"),
        ("sound.aac", b"not-sniffed", "audio/aac"),
        ("movie.mp4", b"\x00\x00\x00 ftypmp42", "video/mp4"),
        ("book.xlsx", b"PK\x03\x04", "application/zip"),
        ("data.parquet", b"PAR1data", "application/vnd.apache.parquet"),
        ("data.sqlite", b"SQLite format 3\x00", "application/vnd.sqlite3"),
        ("archive.zip", b"PK\x03\x04", "application/zip"),
    ]
    for name, data, expected_mime in cases:
        target = tmp_path / name
        target.write_bytes(data)

        payload, mime = filesystem.read_raw(str(target))

        assert payload == data
        assert mime == expected_mime


def test_path_info_returns_sniffed_preview_mime_for_misleading_extension(tmp_path):
    target = tmp_path / "renamed.bin"
    target.write_bytes(b"\x89PNG\r\n\x1a\npayload")

    result = filesystem.path_info(str(target))

    assert result["size"] == target.stat().st_size
    assert result["preview_mime"] == "image/png"


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


def test_list_directory_sorts_dirs_first_then_case_insensitive_name(tmp_path):
    # Entries come back sorted dirs-first, then case-INsensitively by name (from the assembled entry
    # list, not raw os.listdir order). Mixed case + mixed kind exercises both sort keys.
    (tmp_path / "Zebra").mkdir()
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta.txt").write_text("b", encoding="utf-8")
    (tmp_path / "Apple.txt").write_text("a", encoding="utf-8")

    names = [entry["name"] for entry in filesystem.list_directory(str(tmp_path))["entries"]]

    assert names == ["alpha", "Zebra", "Apple.txt", "beta.txt"]


def test_search_files_returns_fuzzy_matches_and_skips_heavy_dirs_inside_repo(tmp_path):
    git(tmp_path, "init")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "hello_x_and_y.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "hello_x_and_y.js").write_text("bad\n", encoding="utf-8")

    payload = filesystem.search_files(str(tmp_path), "xy", 20)

    assert payload["root"] == str(tmp_path)
    paths = [item["relative_path"] for item in payload["files"]]
    assert "src/hello_x_and_y.py" in paths
    assert "node_modules/hello_x_and_y.js" not in paths
    # search hits carry realpath + size so the client can fold symlink/mirror duplicates.
    hit = next(item for item in payload["files"] if item["relative_path"] == "src/hello_x_and_y.py")
    assert hit["realpath"] == os.path.realpath(str(tmp_path / "src" / "hello_x_and_y.py"))
    assert hit["size"] == len("print('ok')\n")


def test_search_files_doit_queries_require_project_doc_prefix(tmp_path):
    git(tmp_path, "init")
    (tmp_path / "DOIT.57.md").write_text("# doit\n", encoding="utf-8")
    (tmp_path / "frontend-crates").mkdir()
    (tmp_path / "frontend-crates" / "DOIT.parser-performance-v2-audit.md").write_text("# audit\n", encoding="utf-8")
    (tmp_path / "static_src" / "js" / "yolomux").mkdir(parents=True)
    (tmp_path / "static_src" / "js" / "yolomux" / "75_dockview_layout.js").write_text("export {}\n", encoding="utf-8")

    broad = filesystem.search_files(str(tmp_path), "DOIT", 20)
    exactish = filesystem.search_files(str(tmp_path), "doit57", 20)

    broad_paths = {item["relative_path"] for item in broad["files"]}
    exactish_paths = {item["relative_path"] for item in exactish["files"]}
    assert "DOIT.57.md" in broad_paths
    assert "frontend-crates/DOIT.parser-performance-v2-audit.md" in broad_paths
    assert "static_src/js/yolomux/75_dockview_layout.js" not in broad_paths
    assert exactish_paths == {"DOIT.57.md"}


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
    git(repo, "init")
    (repo / "src").mkdir()
    (repo / "src" / "deep.py").write_text("print('repo')\n", encoding="utf-8")

    payload = filesystem.search_files(str(root), "", 50)

    paths = {item["relative_path"] for item in payload["files"]}
    assert "top.txt" in paths
    assert "project/src/deep.py" in paths
    assert "notes/nested.md" not in paths
    assert ".cache/cache.txt" not in paths
    assert "project/.git/HEAD" not in paths


def test_search_files_recursive_walks_indexed_non_repo_root_but_skips_heavy_dirs(tmp_path):
    root = tmp_path / "indexed"
    root.mkdir()
    (root / "nested" / "deeper").mkdir(parents=True)
    (root / "nested" / "deeper" / "target_file.py").write_text("print('hit')\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "target_file.js").write_text("skip\n", encoding="utf-8")

    payload = filesystem.search_files(str(root), "target", 20, recursive=True)

    paths = {item["relative_path"] for item in payload["files"]}
    assert "nested/deeper/target_file.py" in paths
    assert "node_modules/target_file.js" not in paths


def test_search_files_canonicalizes_symlink_root(tmp_path):
    real_root = tmp_path / "dynamo" / "notes"
    real_root.mkdir(parents=True)
    (real_root / "DIS-2218.md").write_text("# notes\n", encoding="utf-8")
    link_root = tmp_path / "notes"
    link_root.symlink_to(real_root, target_is_directory=True)

    payload = filesystem.search_files(str(link_root), "DIS-2218", 20, recursive=True)

    assert payload["root"] == str(real_root)
    assert payload["root_realpath"] == os.path.realpath(real_root)
    assert [item["path"] for item in payload["files"]] == [str(real_root / "DIS-2218.md")]
    assert payload["files"][0]["realpath"] == os.path.realpath(real_root / "DIS-2218.md")


def test_search_files_ranks_exact_filename_above_large_generated_sibling(tmp_path):
    root = tmp_path / "dynamo"
    logs = root / "commits" / "logs"
    target_dir = root / "notes" / "tool-calling" / "DIS-1850__jinja-spike"
    logs.mkdir(parents=True)
    target_dir.mkdir(parents=True)
    for index in range(30):
        (logs / f"ea-1850-{index:02d}-premerge.html").write_text("generated\n", encoding="utf-8")
    target = target_dir / "DIS-1850.md"
    target.write_text("# DIS-1850\n", encoding="utf-8")

    payload = filesystem.search_files(str(root), "DIS-1850", 5, recursive=True)

    assert payload["truncated"] is True
    assert payload["files"][0]["relative_path"] == "notes/tool-calling/DIS-1850__jinja-spike/DIS-1850.md"


def test_search_files_matches_absolute_path_segments(tmp_path):
    project = tmp_path / "home" / "keivenc" / "project"
    project.mkdir(parents=True)
    git(project, "init")
    (project / "README.md").write_text("# ok\n", encoding="utf-8")

    payload = filesystem.search_files(str(project), "hokread", 20)

    assert [item["relative_path"] for item in payload["files"]] == ["README.md"]


def test_search_files_marks_generated_upload_names(tmp_path):
    git(tmp_path, "init")
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


def test_read_file_reports_git_tracked(tmp_path):
    # A committed file is tracked; an untracked sibling and a file outside any repo are not.
    # The editor uses this flag to hide its blame/diff buttons for files with no git history.
    init_repo(tmp_path)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("committed\n", encoding="utf-8")
    git(tmp_path, "add", "tracked.txt")
    git(tmp_path, "commit", "-q", "-m", "add")
    untracked = tmp_path / "untracked.txt"
    untracked.write_text("new\n", encoding="utf-8")
    tracked_payload = filesystem.read_file(str(tracked))
    untracked_payload = filesystem.read_file(str(untracked))
    assert tracked_payload["git_tracked"] is True
    assert tracked_payload["git_root"] == str(tmp_path)
    assert tracked_payload["git_has_history"] is False
    assert len(tracked_payload["git_history"]) == 1
    assert untracked_payload["git_root"] == str(tmp_path)
    assert untracked_payload["git_tracked"] is False


def test_read_file_reports_file_level_git_history(tmp_path):
    init_repo(tmp_path)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(tmp_path, "add", "tracked.txt")
    git(tmp_path, "commit", "-q", "-m", "add tracked")
    tracked.write_text("two\n", encoding="utf-8")
    git(tmp_path, "add", "tracked.txt")
    git(tmp_path, "commit", "-q", "-m", "update tracked")

    payload = filesystem.read_file(str(tracked))

    assert payload["git_tracked"] is True
    assert payload["git_has_history"] is True
    assert [item["subject"] for item in payload["git_history"]] == ["update tracked", "add tracked"]
    assert all(item["ref"] and item["short"] for item in payload["git_history"])


def test_read_file_outside_repo_is_not_tracked(tmp_path):
    file_path = tmp_path / "loose.txt"
    file_path.write_text("loose\n", encoding="utf-8")
    payload = filesystem.read_file(str(file_path))
    assert payload["git_root"] == ""
    assert payload["git_tracked"] is False


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
    actual_ns = base_ns + filesystem.MTIME_NS_CONFLICT_TOLERANCE + 1
    os.utime(target, ns=(actual_ns, actual_ns))

    with pytest.raises(FilesystemError) as info:
        filesystem.write_file(str(target), "b", expected_mtime=base_ns)

    assert info.value.status == 409


def test_write_file_accepts_tiny_nanosecond_mtime_drift(tmp_path):
    target = tmp_path / "race-ns-jitter.txt"
    target.write_text("a", encoding="utf-8")
    base_ns = 1_800_000_000_123_456_000
    os.utime(target, ns=(base_ns, base_ns))
    os.utime(target, ns=(base_ns + 85, base_ns + 85))

    result = filesystem.write_file(str(target), "b", expected_mtime=base_ns)

    assert result["size"] == 1


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
    git(tmp_path, "init")
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_text("print('hi')\n", encoding="utf-8")

    result = filesystem.path_info(str(target))

    assert result["repo_root"] == str(tmp_path)
    assert result["relative_path"] == "src/main.py"
    assert result["kind"] == "file"


def test_file_identity_payloads_follow_symlinks_and_hardlinks(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("hello\n", encoding="utf-8")
    symlink = tmp_path / "alias.txt"
    symlink.symlink_to(target)
    hardlink = tmp_path / "hard.txt"
    os.link(target, hardlink)
    broken = tmp_path / "broken.txt"
    broken.symlink_to(tmp_path / "missing.txt")

    target_read = filesystem.read_file(str(target))
    symlink_read = filesystem.read_file(str(symlink))
    hardlink_info = filesystem.path_info(str(hardlink))
    entries = {entry["name"]: entry for entry in filesystem.list_directory(str(tmp_path))["entries"]}
    broken_info = filesystem.path_info(str(broken))

    assert target_read["file_id"]
    assert target_read["file_identity"] == f"id:{target_read['file_id']}"
    assert symlink_read["file_id"] == target_read["file_id"]
    assert symlink_read["realpath"] == os.path.realpath(target)
    assert entries["alias.txt"]["file_id"] == target_read["file_id"]
    assert entries["alias.txt"]["realpath"] == os.path.realpath(target)
    assert hardlink_info["file_id"] == target_read["file_id"]
    assert hardlink_info["realpath"] == os.path.realpath(hardlink)
    assert "file_id" not in broken_info
    assert "file_identity" not in broken_info


def test_diff_file_returns_git_diff_for_tracked_file(tmp_path):
    init_repo(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("print('one')\n", encoding="utf-8")
    git(tmp_path, "add", "app.py")
    git(tmp_path, "commit", "-m", "base")
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
    git(tmp_path, "init")
    target = tmp_path / "new.txt"
    target.write_text("hello\n", encoding="utf-8")

    result = filesystem.diff_file(str(target))

    assert result["relative_path"] == "new.txt"
    assert result["untracked"] is True
    assert result["original"] == ""
    assert "+hello" in result["diff"]


def test_diff_file_returns_head_content_for_deleted_file(tmp_path):
    init_repo(tmp_path)
    target = tmp_path / "gone.txt"
    target.write_text("old\n", encoding="utf-8")
    git(tmp_path, "add", "gone.txt")
    git(tmp_path, "commit", "-m", "base")
    target.unlink()

    result = filesystem.diff_file(str(target))

    assert result["original"] == "old\n"
    assert result["working_missing"] is True
    assert "-old" in result["diff"]


def test_diff_file_supports_commit_to_commit_refs(tmp_path):
    init_repo(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("one\n", encoding="utf-8")
    git(tmp_path, "add", "app.py")
    git(tmp_path, "commit", "-m", "one")
    older = git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    target.write_text("two\n", encoding="utf-8")
    git(tmp_path, "commit", "-am", "two")
    newer = git(tmp_path, "rev-parse", "HEAD").stdout.strip()

    result = filesystem.diff_file(str(target), from_ref=older, to_ref=newer)

    assert result["from_ref"] == older
    assert result["to_ref"] == newer
    assert result["original"] == "one\n"
    assert result["working"] == "two\n"
    assert "-one" in result["diff"]
    assert "+two" in result["diff"]


def test_diff_file_falls_back_when_requested_ref_is_unknown_in_repo(tmp_path):
    init_repo(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("one\n", encoding="utf-8")
    git(tmp_path, "add", "app.py")
    git(tmp_path, "commit", "-m", "one")
    target.write_text("two\n", encoding="utf-8")

    result = filesystem.diff_file(str(target), from_ref="not-in-this-repo", to_ref="current")

    assert result["from_ref"] == "HEAD"
    assert result["to_ref"] == "current"
    assert "-one" in result["diff"]
    assert "+two" in result["diff"]


def test_diff_file_falls_back_when_requested_ref_order_is_invalid(tmp_path):
    init_repo(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("one\n", encoding="utf-8")
    git(tmp_path, "add", "app.py")
    git(tmp_path, "commit", "-m", "one")
    older = git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    target.write_text("two\n", encoding="utf-8")
    git(tmp_path, "commit", "-am", "two")
    newer = git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    target.write_text("three\n", encoding="utf-8")

    result = filesystem.diff_file(str(target), from_ref=newer, to_ref=older)

    assert result["from_ref"] == "HEAD"
    assert result["to_ref"] == "current"
    assert "-two" in result["diff"]
    assert "+three" in result["diff"]


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


def test_parse_blame_porcelain_extracts_author_pr_and_uncommitted():
    sha = "a" * 40
    sample = (
        f"{sha} 1 1 1\n"
        "author Jane Doe\n"
        "author-time 1700000000\n"
        "summary Fix the thing (#42)\n"
        "\tcode line one\n"
        f"{sha} 2 2\n"
        "\tcode line two\n"
        "0000000000000000000000000000000000000000 3 3 1\n"
        "author Not Committed Yet\n"
        "author-time 1700000001\n"
        "summary uncommitted\n"
        "\tuncommitted line\n"
    )
    lines = filesystem._parse_blame_porcelain(sample)
    assert lines["1"]["author"] == "Jane Doe"
    assert lines["1"]["pr"] == 42
    assert lines["1"]["summary"] == "Fix the thing (#42)"
    assert lines["1"]["time"] == 1700000000
    # commit headers appear once; line 2 of the same commit reuses them
    assert lines["2"]["author"] == "Jane Doe"
    assert lines["2"]["summary"] == "Fix the thing (#42)"
    # an all-zero sha is the uncommitted sentinel → "You" / "Uncommitted changes"
    assert lines["3"]["author"] == "You"
    assert lines["3"]["summary"] == "Uncommitted changes"
    assert lines["3"]["pr"] is None


def test_blame_file_on_a_tracked_repo_file():
    # AGENTS.md is committed in this repo; blame should return per-line commit info.
    repo_file = str(Path(__file__).resolve().parents[1] / "AGENTS.md")
    result = filesystem.blame_file(repo_file)
    assert result["in_repo"] is True
    assert result["lines"], "expected per-line blame for a tracked file"
    first = result["lines"]["1"]
    assert len(first["sha"]) == 40
    assert first["author"]


def test_rename_path_uses_git_mv_for_tracked_file(tmp_path):
    # Renaming a git-TRACKED file uses `git mv`, so the new path lands TRACKED (staged), not untracked
    # like a plain rename would leave it.
    def run(*args):
        subprocess.run(args, cwd=str(tmp_path), check=True, capture_output=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "T")
    (tmp_path / "old.txt").write_text("hi\n", encoding="utf-8")
    run("git", "add", "old.txt")
    run("git", "commit", "-qm", "init")

    result = filesystem.rename_path(str(tmp_path / "old.txt"), "new.txt")
    assert result["name"] == "new.txt"
    assert (tmp_path / "new.txt").exists()
    assert not (tmp_path / "old.txt").exists()
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "new.txt"], cwd=str(tmp_path), capture_output=True)
    assert tracked.returncode == 0, "git mv staged the new path (a plain mv would leave it untracked)"


def test_rename_path_plain_rename_for_untracked_file(tmp_path):
    # No repo / untracked: a plain rename still works (git mv path returns False, caller falls back).
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    result = filesystem.rename_path(str(tmp_path / "a.txt"), "b.txt")
    assert result["name"] == "b.txt"
    assert (tmp_path / "b.txt").exists() and not (tmp_path / "a.txt").exists()
    assert filesystem._git_mv_if_tracked(tmp_path / "b.txt", tmp_path / "c.txt") is False


def test_list_directory_flags_symlinks_with_target(tmp_path):
    # symlink entries carry is_symlink + symlink_target; a symlink to a dir resolves kind=dir,
    # to a file kind=file, and a dangling link is kind=symlink-broken. Plain entries are not flagged.
    (tmp_path / "real_dir").mkdir()
    (tmp_path / "real_file.txt").write_text("hi", encoding="utf-8")
    os.symlink(tmp_path / "real_dir", tmp_path / "link_dir")
    os.symlink(tmp_path / "real_file.txt", tmp_path / "link_file")
    os.symlink(tmp_path / "nope", tmp_path / "link_broken")

    payload = filesystem.list_directory(str(tmp_path))
    by_name = {entry["name"]: entry for entry in payload["entries"]}

    assert by_name["link_dir"]["is_symlink"] is True
    assert by_name["link_dir"]["kind"] == "dir"
    assert by_name["link_dir"]["symlink_target"] == str(tmp_path / "real_dir")

    assert by_name["link_file"]["is_symlink"] is True
    assert by_name["link_file"]["kind"] == "file"
    assert by_name["link_file"]["symlink_target"] == str(tmp_path / "real_file.txt")

    assert by_name["link_broken"]["is_symlink"] is True
    assert by_name["link_broken"]["kind"] == "symlink-broken"
    assert by_name["link_broken"]["symlink_target"] == str(tmp_path / "nope")

    assert by_name["real_file.txt"]["is_symlink"] is False
    assert "symlink_target" not in by_name["real_file.txt"]
