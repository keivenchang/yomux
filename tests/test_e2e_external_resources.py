# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Real-browser guard for page-load dependencies served outside YOLOmux."""

from __future__ import annotations

from typing import Any

import pytest


pytest_plugins = ("tests.e2e_browser_harness",)
pytestmark = [pytest.mark.browser, pytest.mark.socket, pytest.mark.e2e]


def test_authenticated_page_loads_no_external_resources(e2e_browser: Any) -> None:
    """The normal application shell remains functional without public CDNs."""
    e2e_browser.load(tabs=("files", str(e2e_browser.runtime.tmux.sessions[0])))

    external_hosts = e2e_browser.driver.execute_script(
        """
        return performance.getEntriesByType('resource')
          .map(entry => new URL(entry.name))
          .filter(url => url.origin !== location.origin)
          .map(url => url.host);
        """
    )

    assert external_hosts == [], external_hosts
