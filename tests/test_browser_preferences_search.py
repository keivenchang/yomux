# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select, WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401


@pytest.mark.browser
def test_native_preferences_search_matches_translated_terms_and_rejects_unmatched_queries(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('preferences-search')
    request.addfinalizer(lambda: browser.get('about:blank'))
    browser.get(server.base_url + '/?sessions=__prefs__')
    wait = WebDriverWait(browser, 20)
    wait.until(lambda driver: driver.find_elements(By.CSS_SELECTOR, '[data-preferences-search]'))
    modifier = Keys.COMMAND if browser.capabilities['platformName'].lower() in {'mac', 'macos', 'darwin'} else Keys.CONTROL

    def search(query):
        field = browser.find_element(By.CSS_SELECTOR, '[data-preferences-search]')
        field.send_keys(modifier, 'a')
        field.send_keys(Keys.BACKSPACE)
        for character in query:
            browser.find_element(By.CSS_SELECTOR, '[data-preferences-search]').send_keys(character)
        wait.until(lambda driver: driver.execute_script('return preferencesSearchText===arguments[0]', query))

    search('language')
    Select(browser.find_element(By.CSS_SELECTOR, 'select[data-setting-path="general.language"]')).select_by_value('zh-Hant')
    # The optimistic locale renders before the saved settings revision can replace its DOM.
    wait.until(lambda driver: driver.execute_script("""
      return i18nActiveLocaleId()==='zh-Hant' && initialSetting('general.language','system')==='zh-Hant';
    """))
    search('不存在的偏好篩選詞')
    assert browser.find_elements(By.CSS_SELECTOR, '.preferences-section') == []
    search('主題 全域')
    assert browser.find_elements(By.CSS_SELECTOR, '[data-setting-path="appearance.theme"]')
    search('?')
    assert browser.execute_script("""
      return [...document.querySelectorAll('.preferences-section')].every(section=>{
        const spec=preferenceSections().find(item=>item.id===section.dataset.preferenceSection);
        return textMatchesPreferenceQuery(spec.title,'?') || spec.items.some(item=>preferenceItemMatches(item,'?'));
      });
    """)
