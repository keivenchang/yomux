# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_layout import select_current_value
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401


@pytest.mark.browser
def test_native_preferences_search_matches_translated_terms_and_rejects_unmatched_queries(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('preferences-search')
    request.addfinalizer(lambda: browser.get('about:blank'))
    browser.get(server.base_url + '/?sessions=__prefs__')
    wait = WebDriverWait(browser, 20)
    wait.until(lambda driver: driver.find_elements(By.CSS_SELECTOR, '[data-preferences-search]'))

    def search(query):
        assert browser.execute_script("""
          const input = document.querySelector('[data-preferences-search]');
          input.focus({preventScroll:true});
          input.select();
          return document.activeElement === input;
        """)
        browser.execute_cdp_cmd('Input.insertText', {'text': query})
        wait.until(lambda driver: driver.execute_script("""
          return preferencesSearchText === arguments[0] &&
            document.querySelector('[data-preferences-search]')?.value === arguments[0];
        """, query))

    search('language')
    browser.execute_script("""
      const save=saveSettingsPatch, locale=applyLocale;
      saveSettingsPatch=(...args)=>(window.__prefsSave=save(...args));
      applyLocale=(...args)=>(window.__prefsLocale=locale(...args));
    """)
    select_current_value(browser, 'select[data-setting-path="general.language"]', 'zh-Hant')
    # Both the save response and its locale application can replace the search input.
    result = browser.execute_async_script("""
      const done=arguments[arguments.length-1];
      Promise.resolve(window.__prefsSave).then(()=>window.__prefsLocale)
        .then(()=>done('settled'),error=>done(String(error)));
    """)
    assert result == 'settled', result
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
