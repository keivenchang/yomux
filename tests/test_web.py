"""Dedicated coverage for yolomux_lib.web — the server-rendered HTML shell and its escaping.

web.py had no test file of its own; its HTML-escaping / <script>-breakout safety was only exercised
incidentally elsewhere. These tests pin the security-relevant invariants: user-controlled values (tmux
session names) embedded in the page must not be able to break out of the bootstrap <script> tag, and the
<html> lang/dir attributes must be escaped.
"""

import json
import re

from yolomux_lib import common
from yolomux_lib import web

_BOOTSTRAP_RE = re.compile(r'<script id="yolomux-bootstrap"[^>]*>(.*?)</script>', re.DOTALL)


def _bootstrap_json(page: str) -> str:
    match = _BOOTSTRAP_RE.search(page)
    assert match, "page must contain the bootstrap <script> tag"
    return match.group(1)


def test_html_page_escapes_script_breakout_in_session_names():
    # A tmux session name is user-controlled and is embedded in the bootstrap JSON. A name containing
    # </script> + markup must NOT break out of the <script> element: the breakout chars are JSON-unicode
    # escaped (< …), so the only </script> in the bootstrap region is its own closing tag.
    evil = "</script><img src=x onerror=alert(1)>"
    page = web.html_page([evil])
    bootstrap = _bootstrap_json(page)

    assert "</script>" not in bootstrap
    assert "<img" not in bootstrap
    assert "\\u003c/script\\u003e" in bootstrap
    # JSON.parse (json.loads here) round-trips the escaped text back to the literal session name.
    assert evil in json.loads(bootstrap)["sessions"]


def test_html_page_bootstrap_uses_unicode_escapes_not_html_entities():
    # The breakout chars are JSON \u escapes, never HTML entities (which a <script> body would NOT decode,
    # leaving literal &lt; inside parsed strings).
    bootstrap = _bootstrap_json(web.html_page([]))
    assert "&lt;" not in bootstrap and "&gt;" not in bootstrap and "&amp;" not in bootstrap


def test_html_lang_dir_attrs_escapes_and_sets_direction():
    assert web.html_lang_dir_attrs("en") == 'lang="en" dir="ltr"'
    assert web.html_lang_dir_attrs("ar") == 'lang="ar" dir="rtl"'  # RTL locale
    # A hostile locale string cannot inject a raw quote/bracket into the attribute list.
    attrs = web.html_lang_dir_attrs('"><script>')
    assert "<script>" not in attrs
    assert '"><' not in attrs


def test_server_string_normalizes_locale_before_cached_catalog_lookup(monkeypatch, tmp_path):
    locale_dir = tmp_path / "locales"
    locale_dir.mkdir()
    (locale_dir / "en.json").write_text('{"label": "English"}', encoding="utf-8")
    (locale_dir / "zh-Hant.json").write_text('{"label": "繁體中文"}', encoding="utf-8")
    monkeypatch.setattr(web, "STATIC_DIR", tmp_path)
    web.bootstrap_locale_catalogs.cache_clear()
    try:
        assert web.server_string("ZH-hant", "label") == "繁體中文"
        assert web.server_string("zh-Hant", "label") == "繁體中文"
        assert web.bootstrap_locale_catalogs.cache_info().hits >= 1
    finally:
        web.bootstrap_locale_catalogs.cache_clear()


def test_html_page_marks_readonly_role_without_breaking_out():
    # The access role is reflected into the bootstrap payload; a readonly guest renders a valid page.
    bootstrap = _bootstrap_json(web.html_page([], access_role="readonly"))
    assert json.loads(bootstrap)["accessRole"] == "readonly"


def test_html_page_bootstrap_includes_linear_issue_base_url():
    bootstrap = json.loads(_bootstrap_json(web.html_page([])))

    assert bootstrap["linearIssueBaseUrl"] == "https://linear.app/issue"


def test_xterm_unicode11_addon_asset_resolves_from_sibling_package(monkeypatch, tmp_path):
    xterm_root = tmp_path / "@xterm" / "xterm"
    addon_path = tmp_path / "@xterm" / "addon-unicode11" / "lib" / "addon-unicode11.js"
    xterm_root.mkdir(parents=True)
    addon_path.parent.mkdir(parents=True)
    addon_path.write_text("window.Unicode11Addon = {};", encoding="utf-8")

    monkeypatch.setattr(common, "XTERM_ASSET_ROOTS", [xterm_root])

    assert common.xterm_asset_path("xterm-addon-unicode11.js") == addon_path
    assert web.static_content_type("xterm-addon-unicode11.js") == "application/javascript; charset=utf-8"


def test_xterm_asset_path_prefers_packaged_static_asset(monkeypatch, tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    packaged_asset = static_dir / "xterm.js"
    packaged_asset.write_text("window.Terminal = {};", encoding="utf-8")
    monkeypatch.setattr(common, "STATIC_DIR", static_dir)

    assert common.xterm_asset_path("xterm.js") == packaged_asset


def test_html_page_loads_xterm_unicode11_addon_after_xterm():
    page = web.html_page([])

    xterm_index = page.index("/static/xterm.js")
    addon_index = page.index("/static/xterm-addon-unicode11.js")
    bootstrap_index = page.index('id="yolomux-bootstrap"')

    assert xterm_index < addon_index < bootstrap_index
    assert "cdn.jsdelivr.net/npm/@xterm/addon-unicode11/lib/addon-unicode11.js" in page
