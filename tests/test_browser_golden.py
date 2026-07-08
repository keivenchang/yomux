"""Small local-only visual-regression matrix for the app's highest-level surfaces.

These are intentionally not checked-in test fixtures.  The normal assertion compares each page to
the baseline reviewed on this developer machine; `YOLOMUX_UPDATE_GOLDENS=1` refreshes all 20 images.
The pages still use the shipped CSS and bundle so this catches a bad layout, shadow, gradient, or
whole-chrome regression that individual computed-style assertions can miss.
"""

from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401


GOLDEN_PROFILES = (("dark", 1), ("dark", 2), ("light", 1), ("light", 2))
GOLDEN_PAGES = ("grid", "dockview", "finder-differ", "preferences", "share-replay")


def _golden_name(page, theme, dpr):
    return f"{page}-{theme}-dpr{dpr}"


def _load_golden_page(browser, tmp_path, page):
    if page == "grid":
        load_live_runtime_boot_fixture(
            browser,
            tmp_path,
            "?sessions=1,2&layout=row@50(left,right)&tabs=left:1;right:2",
            sessions=["1", "2"],
        )
        WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return document.querySelectorAll('.panel').length >= 2"))
        return
    if page == "dockview":
        load_dockview_runtime_boot_fixture(
            browser,
            tmp_path,
            "?sessions=1,2&layout=row@50(left,right)&tabs=left:1;right:2",
            sessions=["1", "2"],
        )
        wait_for_dockview(browser, min_tabs=2)
        return
    if page == "finder-differ":
        load_live_runtime_boot_fixture(
            browser,
            tmp_path,
            "?sessions=changes,1&layout=row@35(left,right)&tabs=left:changes;right:1",
            sessions=["1"],
        )
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script("return document.querySelector('#panel-__files__')?.dataset.fileExplorerMode === 'diff'")
        )
        return
    if page == "preferences":
        load_live_runtime_boot_fixture(
            browser,
            tmp_path,
            "?sessions=1,__prefs__&layout=row@50(left,right)&tabs=left:1;right:__prefs__",
            sessions=["1"],
        )
        WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return document.querySelector('#panel-__prefs__ .preferences-section')"))
        return
    if page == "share-replay":
        share_bootstrap = {
            "view": True,
            "id": "golden-share-replay",
            "mode": "ro",
            "session": "1",
            "sessions": ["1"],
            "createdBy": "golden-host",
            "expiresAt": 4102444800.0,
            "maxViewers": 5,
            "layout": "left",
            "tabs": "left:1",
            "uiState": {"layout": "left", "tabs": "left:1", "viewport": {"width": 1000, "height": 700}},
        }
        load_live_runtime_boot_fixture(
            browser,
            tmp_path,
            search="#t=golden-share-token",
            sessions=["1"],
            access_role="readonly",
            share_bootstrap=share_bootstrap,
            share_status_payload={"ok": True, "active": True, "token": "golden-share-token", "mode": "ro", "expiresAt": 4102444800.0, "uiState": share_bootstrap["uiState"]},
            wrap_app_root=True,
        )
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script("return document.body.classList.contains('share-replay-shell') && document.querySelector('.share-replay-stage, #appRoot')")
        )
        return
    raise ValueError(f"unknown golden page: {page}")


@pytest.mark.parametrize("theme,dpr", GOLDEN_PROFILES, ids=lambda value: str(value))
@pytest.mark.parametrize("page", GOLDEN_PAGES)
def test_local_canonical_visual_goldens(browser, tmp_path, page, theme, dpr):
    _load_golden_page(browser, tmp_path, page)
    profile = set_browser_visual_profile(browser, theme=theme, dpr=dpr)
    assert profile["dpr"] == dpr, profile
    freeze_document_animations(browser)
    browser.execute_async_script(
        """
        const done = arguments[0];
        window.__yolomuxTestHelpers.settle(3).then(() => done(true));
        """
    )
    result = assert_local_golden_screenshot(browser, _golden_name(page, theme, dpr))
    assert result["status"] in {"matched", "updated"}, result
