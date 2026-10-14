# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import pytest

from tests.browser_helpers.browser_layout import browser, load_live_runtime_boot_fixture
from selenium.webdriver.support.ui import WebDriverWait

pytestmark = [pytest.mark.browser, pytest.mark.socket]


def test_finder_demand_tracks_active_tab_and_page_visibility(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("return typeof clientServerWatchState === 'function' && typeof renderPanels === 'function'"))
    browser.execute_script("""
      const slots = emptyLayoutSlots();
      slots.left = paneStateWithTabs([finderItemId, tabberItemId], finderItemId);
      applyLayoutSlots(slots, {prune: false, forceFull: true});
    """)
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("return itemIsActivePaneTab(finderItemId) && itemInLayout(tabberItemId)"))
    visible = browser.execute_script("return clientServerWatchState()")
    browser.execute_script("activatePaneTab(slotForItem(tabberItemId), tabberItemId, {userInitiated: true})")
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return itemIsActivePaneTab(tabberItemId) && !itemIsActivePaneTab(finderItemId)"))
    inactive = browser.execute_script("return clientServerWatchState()")
    browser.execute_script("activatePaneTab(slotForItem(finderItemId), finderItemId, {userInitiated: true})")
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return itemIsActivePaneTab(finderItemId)"))
    hidden = browser.execute_script("Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'hidden'}); return clientServerWatchState()")
    reopened = browser.execute_script("Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'}); return clientServerWatchState()")
    assert visible["roots"]
    assert inactive["roots"] == []
    assert hidden["roots"] == []
    assert visible["client_id"] == hidden["client_id"] != ""
    assert reopened["roots"] == visible["roots"]


def test_quick_open_reports_only_requested_scopes_with_panels_hidden(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    result = browser.execute_script("""
      const slots = emptyLayoutSlots(); slots.left = paneStateWithTabs(['1'], '1');
      layoutSlots = slots; renderPanels([], {prune: false});
      const before = clientServerWatchState();
      openCommandPalette('files');
      fileQuickOpenState.deltaRoots = new Map([['/home/test/project', {}]]);
      const searching = clientServerWatchState();
      closeCommandPalette();
      const closed = clientServerWatchState();
      return {before, searching, closed};
    """)
    assert result["before"]["roots"] == result["searching"]["roots"] == []
    assert result["searching"]["search_roots"] == ["/home/test/project"]
    assert result["closed"]["search_roots"] == []
