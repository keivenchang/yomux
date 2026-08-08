# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Cross-panel gate for user-visible unresolved localization keys."""

from __future__ import annotations

import pytest

from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401
from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import wait_for_browser_boot
from tests.test_gate_editor import gate_browser_runtime
from tests.test_gate_local_services import local_services_source_bundle  # noqa: F401


pytestmark = [pytest.mark.browser, pytest.mark.socket]


def test_rendered_ui_has_no_recorded_raw_i18n_keys(local_services_source_bundle, gate_browser_runtime):
    """Rendered UI text may not equal a key the i18n runtime failed to resolve."""

    browser = gate_browser_runtime.browser
    wait_for_browser_boot(browser, globals_required={"i18nMissingKeyList": "function"}, dom_anchors=("#grid",), timeout=12)
    baseline = browser.execute_script("return i18nMissingKeyList();")
    assert baseline == [], baseline
    forced = browser.execute_script(
        """
        i18nSetCatalogForTest('en', {});
        const host = document.createElement('div');
        host.innerHTML = debugSystemLocalServicesCardHtml();
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
    assert "debug.system.localServices.title" in forced["keys"], forced
    assert any(item["key"] == "debug.system.localServices.title" for item in forced["raw"]), forced
