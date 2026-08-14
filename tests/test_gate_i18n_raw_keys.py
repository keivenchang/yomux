# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Cross-panel gate for user-visible unresolved localization keys."""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401
from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import wait_for_browser_boot
from tests.test_gate_editor import gate_browser_runtime
from tools import static_build
from yolomux_lib import web as web_module


pytestmark = [pytest.mark.browser, pytest.mark.socket]

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def i18n_source_bundle(monkeypatch, tmp_path):
    """Serve this gate from a fixture-owned bundle built from current source.

    It used to live in `tests/test_gate_local_services.py`, beside the retired per-cell Local
    services table this gate borrowed as its render vehicle. That table is gone; the fixture is
    not, because the point of it is that the bundle under test is built from the working tree
    rather than from whatever `static/` happens to hold.
    """
    asset_dir = tmp_path / "i18n-static"
    asset_dir.mkdir()
    for name in ("brand.css", "codemirror.js", "yolomux.css"):
        shutil.copy2(REPO_ROOT / "static" / name, asset_dir / name)
    for name in ("fonts", "locales", "vendor"):
        shutil.copytree(REPO_ROOT / "static" / name, asset_dir / name)
    (asset_dir / "yolomux.js").write_text(static_build.build_asset("yolomux.js"), encoding="utf-8")
    monkeypatch.setattr(web_module, "STATIC_DIR", asset_dir)


def test_rendered_ui_has_no_recorded_raw_i18n_keys(i18n_source_bundle, gate_browser_runtime):
    """Rendered UI text may not equal a key the i18n runtime failed to resolve.

    The render vehicle is the Daemons roster header, which is what the retired Local services card
    used to be. It is a better one: the roster is the DEFAULT Daemons view, so this gate now drives
    a surface a reader actually sees rather than a fallback that only an unsupported schema reached.
    """

    browser = gate_browser_runtime.browser
    wait_for_browser_boot(browser, globals_required={"i18nMissingKeyList": "function"}, dom_anchors=("#grid",), timeout=12)
    baseline = browser.execute_script("return i18nMissingKeyList();")
    assert baseline == [], baseline
    forced = browser.execute_script(
        """
        i18nSetCatalogForTest('en', {});
        const host = document.createElement('div');
        host.innerHTML = debugSystemRosterHtml({local_services: {schema_version: 3, inventory: [], services: []}}, {nowSeconds: 0});
        document.body.append(host);
        const keys = i18nMissingKeyList();
        const raw = [...document.querySelectorAll('body *')].flatMap(element => {
          if (element.children.length || !keys.includes(element.textContent.trim())) return [];
          return [{key: element.textContent.trim(), tag: element.tagName, className: element.className}];
        });
        host.remove();
        return {keys, raw};
        """
    )
    assert "debug.system.roster.column.service" in forced["keys"], forced
    assert any(item["key"] == "debug.system.roster.column.service" for item in forced["raw"]), forced
