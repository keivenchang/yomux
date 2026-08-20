"""Dedicated coverage for yolomux_lib.web — the server-rendered HTML shell and its escaping.

web.py had no test file of its own; its HTML-escaping / <script>-breakout safety was only exercised
incidentally elsewhere. These tests pin the security-relevant invariants: user-controlled values (tmux
session names) embedded in the page must not be able to break out of the bootstrap <script> tag, and the
<html> lang/dir attributes must be escaped.
"""

import json
from pathlib import Path
import re

from yolomux_lib import common
from yolomux_lib import web
from yolomux_lib.stats_current import storage as stats_current_storage

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


def test_html_page_declares_inline_favicon_before_browser_can_request_default():
    page = web.html_page([])

    # One inline favicon, declared before any stylesheet, so the browser never probes the
    # authenticated /favicon.ico. Assert the intent rather than one literal href: a9b24fc4b
    # and be136e9aa each fixed this independently with different hrefs, and pinning the
    # exact string is what made those two fixes look like a conflict.
    favicons = re.findall(r'<link rel="icon"[^>]*>', page)
    assert len(favicons) == 1, favicons
    assert 'data-yolomux-favicon' in favicons[0]
    assert 'href="data:' in favicons[0]
    assert page.index(favicons[0]) < page.index('/static/yolomux.css')


def test_html_page_bootstraps_the_server_owned_stats_writer_fence():
    bootstrap = json.loads(_bootstrap_json(web.html_page([])))

    assert bootstrap["statsWriterFence"] == {
        "protocol_version": stats_current_storage.MIN_WRITER_PROTOCOL,
        "schema_generation": stats_current_storage.SCHEMA_VERSION,
    }


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


def test_html_page_bootstraps_authoritative_username():
    bootstrap = json.loads(_bootstrap_json(web.html_page([], auth_username="alice")))

    assert bootstrap["authUsername"] == "alice"


def test_html_page_bootstraps_host_cpu_topology(monkeypatch):
    monkeypatch.setattr(web, "cpu_topology", lambda: {"logical_cpus": 32, "physical_cores": 24})

    bootstrap = json.loads(_bootstrap_json(web.html_page([])))

    assert bootstrap["cpuTopology"] == {"logical_cpus": 32, "physical_cores": 24}


def test_html_page_bootstrap_includes_linear_issue_base_url():
    bootstrap = json.loads(_bootstrap_json(web.html_page([])))

    assert bootstrap["linearIssueBaseUrl"] == "https://linear.app/issue"


def test_html_page_bootstrap_preserves_server_ranked_recent_sessions():
    bootstrap = json.loads(_bootstrap_json(web.html_page(["old", "new"], recent_sessions=["new", "old"])))

    assert bootstrap["recentSessions"] == ["new", "old"]


def test_xterm_assets_have_one_vendor_owner_even_with_root_contamination(monkeypatch, tmp_path):
    static_dir = tmp_path / "static"
    vendor_dir = static_dir / "vendor"
    vendor_dir.mkdir(parents=True)
    names = ("xterm.js", "xterm.css", "xterm-addon-unicode11.js")
    for name in names:
        (vendor_dir / name).write_text(f"vendor-{name}", encoding="utf-8")
        (static_dir / name).write_text(f"contaminated-{name}", encoding="utf-8")
    monkeypatch.setattr(common, "STATIC_DIR", static_dir)
    monkeypatch.setattr(web, "STATIC_DIR", static_dir)

    for name in names:
        assert common.xterm_asset_path(name) == vendor_dir / name
        assert web.static_asset_path(name) == vendor_dir / name
        assert web.static_asset_path(f"vendor/{name}") == vendor_dir / name
        assert web.static_asset_version(name) == web.static_asset_version(f"vendor/{name}")


def test_emoji_catalog_is_a_served_lazy_javascript_asset():
    assert web.static_content_type("emoji-data.js") == "application/javascript; charset=utf-8"
    assert web.static_asset_path("emoji-data.js") == common.STATIC_DIR / "emoji-data.js"


def test_xterm_asset_path_requires_tracked_vendor_asset(monkeypatch, tmp_path):
    static_dir = tmp_path / "static"
    vendor_dir = static_dir / "vendor"
    vendor_dir.mkdir(parents=True)
    packaged_asset = vendor_dir / "xterm.js"
    packaged_asset.write_text("window.Terminal = {};", encoding="utf-8")
    monkeypatch.setattr(common, "STATIC_DIR", static_dir)

    assert common.xterm_asset_path("xterm.js") == packaged_asset
    packaged_asset.unlink()
    assert common.xterm_asset_path("xterm.js") is None


def test_tracked_xterm_vendor_assets_match_pinned_package_fixture():
    package_root = Path("/opt/xterm/node_modules")
    package_paths = {
        "xterm.js": package_root / "@xterm" / "xterm" / "lib" / "xterm.js",
        "xterm.css": package_root / "@xterm" / "xterm" / "css" / "xterm.css",
        "xterm-addon-unicode11.js": package_root / "@xterm" / "addon-unicode11" / "lib" / "addon-unicode11.js",
    }
    assert all(path.is_file() for path in package_paths.values())
    assert {
        name: (common.STATIC_DIR / "vendor" / name).read_bytes()
        for name in package_paths
    } == {
        name: path.read_bytes()
        for name, path in package_paths.items()
    }


def test_html_page_loads_xterm_unicode11_addon_after_xterm():
    page = web.html_page([])

    xterm_index = page.index("/static/vendor/xterm.js")
    addon_index = page.index("/static/vendor/xterm-addon-unicode11.js")
    bootstrap_index = page.index('id="yolomux-bootstrap"')

    assert xterm_index < addon_index < bootstrap_index
    assert "/static/vendor/xterm.css" in page
    assert "cdn.jsdelivr.net/npm/@xterm" not in page
    assert web.static_content_type("vendor/xterm.css") == "text/css; charset=utf-8"
    assert web.static_content_type("vendor/xterm.js") == "application/javascript; charset=utf-8"
    assert web.static_content_type("vendor/xterm-addon-unicode11.js") == "application/javascript; charset=utf-8"
    assert web.static_asset_path("vendor/xterm.js") == common.STATIC_DIR / "vendor" / "xterm.js"


def test_html_page_declares_an_initial_favicon_without_a_network_request():
    """The browser must not probe the authenticated root /favicon.ico before boot code runs."""
    page = web.html_page([])

    favicons = re.findall(r'<link rel="icon"[^>]*>', page)
    assert len(favicons) == 1, favicons
    assert 'href="data:' in favicons[0], favicons[0]


def test_every_page_head_declares_the_one_inline_favicon():
    """Pre-auth pages need this most: there a /favicon.ico probe returns 401.

    a9b24fc4b and be136e9aa each fixed html_page() only, so the login page kept
    emitting the 401 through both fixes. Cover every head-bearing template.
    """
    pages = {
        "html_page": web.html_page([]),
        "login_html": web.login_html(),
        "setup_auth_html": web.setup_auth_html(),
    }
    for name, page in pages.items():
        favicons = re.findall(r'<link rel="icon"[^>]*>', page)
        assert len(favicons) == 1, (name, favicons)
        assert favicons[0] == web.INLINE_FAVICON_LINK, name
        assert page.index(favicons[0]) < page.index("</head>"), name


def test_every_page_head_uses_the_shared_safe_area_viewport_owner():
    pages = {
        "html_page": web.html_page([]),
        "login_html": web.login_html(),
        "setup_auth_html": web.setup_auth_html(),
    }
    assert web.MOBILE_VIEWPORT_CONTENT == "width=device-width, initial-scale=1, viewport-fit=cover"
    for name, page in pages.items():
        viewport_meta = re.findall(r'<meta name="viewport"[^>]*>', page)
        assert viewport_meta == [web.MOBILE_VIEWPORT_META], name
