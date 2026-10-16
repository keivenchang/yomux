# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Non-browser isolation checks for generated fixture pages and static mounts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from urllib.error import HTTPError
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.browser_helpers import browser_layout
from tests.helpers.fixture_content_root import FixtureContentRoot


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_frozen_diff_fixtures_preserve_historical_bytes_without_git(monkeypatch, tmp_path):
    snapshot_path = Path(browser_layout.__file__).with_name("frozen_diff_snapshots.json")
    declared_snapshot = tmp_path / snapshot_path.name
    declared_snapshot.write_bytes(snapshot_path.read_bytes())
    monkeypatch.setattr(browser_layout, "__file__", str(tmp_path / "browser_layout.py"))
    monkeypatch.chdir(tmp_path)

    def refuse_git(*args, **kwargs):
        raise AssertionError("frozen browser fixtures must not execute Git")

    monkeypatch.setattr(subprocess, "check_output", refuse_git)
    expected = {
        "05f22a8646:TODO.md": "9c70ce51cf7d2f32e9a899c44d279ee989f6a4aff217566003446c8ef7cae9f4",
        "521bbfd:static_src/js/yolomux/40_file_explorer_files.js": "08c94b14805180fe5a2ddf079a8df3473f0d5c19587880230916440527d668e8",
        "6a967aaa70:static_src/js/yolomux/40_file_explorer_files.js": "ece03ebc37dea4917d19e7e2b9e99a8cf7dd8c41c04f96e03ce9bde10fd7d22b",
    }
    for source, digest in expected.items():
        assert hashlib.sha256(browser_layout.frozen_source_text(source).encode("utf-8")).hexdigest() == digest
    original, current = browser_layout.codemirror_todo_diff_overview_texts()
    assert original == browser_layout.frozen_source_text("05f22a8646:TODO.md")
    assert len(current.splitlines()) == 124
    pages = [browser_layout.codemirror_todo_diff_overview_fixture_html(),
             browser_layout.codemirror_file_explorer_diff_overview_fixture_html()]
    for source in expected:
        encoded = json.dumps(browser_layout.frozen_source_text(source)).replace("</script", "<\\/script")
        assert any(encoded in page for page in pages)
    assert not (tmp_path / ".git").exists()


def test_identical_worker_sequence_and_name_are_isolated_by_process_root(tmp_path):
    first = FixtureContentRoot.create(REPO_ROOT, parent=tmp_path)
    second = FixtureContentRoot.create(REPO_ROOT, parent=tmp_path)
    try:
        first_page = first.write_page("gw0", "same.html", "first")
        second_page = second.write_page("gw0", "same.html", "second")

        assert first_page.name == second_page.name == "browser-fixture-gw0-1-same.html"
        assert first_page != second_page
        assert first_page.read_text() == "first"
        assert second_page.read_text() == "second"
        assert first.url_path(first_page) == second.url_path(second_page) == "/browser-fixture-gw0-1-same.html"
    finally:
        first.cleanup()
        second.cleanup()


def test_one_process_concurrent_identical_names_get_unique_urls_and_bytes(tmp_path):
    owner = FixtureContentRoot.create(REPO_ROOT, parent=tmp_path)
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            pages = list(executor.map(lambda index: owner.write_page("gw0", "same.html", f"body-{index}"), range(32)))

        assert len(set(pages)) == 32
        assert {page.read_text() for page in pages} == {f"body-{index}" for index in range(32)}
    finally:
        owner.cleanup()


def test_repo_static_assets_are_read_only_resolutions_on_the_same_origin_path(tmp_path):
    repo = tmp_path / "repo"
    (repo / "static").mkdir(parents=True)
    asset = repo / "static" / "bundle.js"
    asset.write_bytes(b"window.fixture = true;\n")
    owner = FixtureContentRoot.create(repo, parent=tmp_path)
    try:
        page = owner.write_page("main", "relative.html", '<script src="/static/bundle.js"></script>')

        assert owner.resolve_request_path(owner.url_path(page)) == page
        assert owner.resolve_request_path("/static/bundle.js") == asset
        assert owner.resolve_request_path("/static/bundle.js").read_bytes() == b"window.fixture = true;\n"
        assert owner.resolve_request_path("/../static/bundle.js") is None
        assert owner.resolve_request_path("/static/../secret") is None
        assert not any(path.name.startswith(".browser-fixture-") for path in repo.iterdir())
    finally:
        owner.cleanup()


def test_process_crash_leaves_no_repository_fixture_artifact(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    parent = tmp_path / "private-roots"
    parent.mkdir()
    code = """
import json, os
from pathlib import Path
from tests.helpers.fixture_content_root import FixtureContentRoot
owner = FixtureContentRoot.create(Path(os.environ['FIXTURE_REPO']), parent=Path(os.environ['FIXTURE_PARENT']))
page = owner.write_page('gw0', 'crash.html', 'crash-body')
print(json.dumps({'root': str(owner.root), 'page': str(page)}), flush=True)
os._exit(17)
"""
    environment = {**os.environ, "FIXTURE_REPO": str(repo), "FIXTURE_PARENT": str(parent)}
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, env=environment,
        capture_output=True, text=True, timeout=30, check=False,
    )

    assert completed.returncode == 17
    evidence = json.loads(completed.stdout)
    assert Path(evidence["page"]).read_text() == "crash-body"
    assert Path(evidence["root"]).parent == parent
    assert list(repo.iterdir()) == []


def test_integrated_fixture_http_origin_preserves_pages_static_special_endpoints_and_cache_headers():
    html = '<!doctype html><script src="/static/brand.css"></script>'
    page = browser_layout.serve_repo_fixture_page("http-contract.html", html)
    page_url = browser_layout.fixture_page_url(page)
    origin = page_url.rsplit("/", 1)[0]

    with urllib.request.urlopen(page_url) as response:
        assert response.read() == html.encode("utf-8")
        assert response.headers["Cache-Control"] == "no-store, must-revalidate"
    with urllib.request.urlopen(f"{origin}/static/brand.css") as response:
        assert response.read() == (REPO_ROOT / "static" / "brand.css").read_bytes()
        assert response.headers["Cache-Control"] == "no-store, must-revalidate"
    with urllib.request.urlopen(f"{origin}/login") as response:
        assert b"fixture login" in response.read()
        assert response.headers["Cache-Control"] == "no-store, must-revalidate"
    with urllib.request.urlopen(f"{origin}/api/fs/raw?path=fixture.svg") as response:
        assert response.read() == b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"></svg>'
        assert response.headers["Content-Type"] == "image/svg+xml"
        assert response.headers["Cache-Control"] == "no-store, must-revalidate"
    with urllib.request.urlopen(f"{origin}/api/fs/fast/list?path=%2F") as response:
        assert json.loads(response.read()) == {"path": "/", "parent": None, "entries": []}
    try:
        urllib.request.urlopen(f"{origin}/api/fs/fast/list?path=%2Fmissing")
    except HTTPError as error:
        assert error.code == 404
    else:
        raise AssertionError("fixture filesystem route must reject non-root paths")
    with urllib.request.urlopen(f"{origin}/favicon.ico") as response:
        assert response.status == 204
        assert response.read() == b""
        assert response.headers["Cache-Control"] == "no-store, must-revalidate"
