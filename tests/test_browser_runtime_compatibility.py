# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import json

import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_console import assert_only_expected_browser_warning
from tests.gate_harness import run_fixture_cleanup_phases
from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.native_pointer import click_visible_selector
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401


@pytest.mark.browser
def test_startup_warns_about_low_inotify_limits_and_allows_dismissal(browser, isolated_dev_server_factory):
    server = isolated_dev_server_factory('inotify-warning')
    command = ('sudo sysctl -w fs.inotify.max_user_instances=1024 '
               'fs.inotify.max_queued_events=65536')
    warning = {'code': 'inotify_limits_low', 'message_key': 'runtime.warning.inotifyLimits',
               'message_params': {'limits': 'fs.inotify.max_user_instances=128, fs.inotify.max_queued_events=16384',
                                  'command': command}, 'timeout_ms': 7_200_000}
    source = '''
      const originalFetch = window.fetch;
      window.__inotifyWarningRequests = 0;
      window.fetch = (input, options) => {
        if (new URL(String(input), location.href).pathname === '/api/runtime/compatibility') {
          window.__inotifyWarningRequests++;
          return Promise.resolve(Response.json({probe_status:'tmux_unavailable',warnings:[WARNING]}));
        }
        return originalFetch(input, options);
      };
    '''.replace('WARNING', json.dumps(warning))
    registration = browser.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {'source': source})['identifier']
    try:
        browser.get(server.base_url + '/?sessions=' + server.tmux.sessions[0])
        wait = WebDriverWait(browser, 20)
        node = wait.until(lambda driver: next((item for item in driver.find_elements(By.CSS_SELECTOR, '.runtime-compatibility-warning') if item.is_displayed()), None))
        assert_only_expected_browser_warning(browser, message='Runtime compatibility probe unavailable:', correlation='"tmux_unavailable"')
        assert 'fs.inotify.max_user_instances=128' in node.text
        assert 'fs.inotify.max_queued_events=16384' in node.text
        assert command in node.text
        assert 'running system only' in node.text
        browser.execute_async_script('const done=arguments[0];checkRuntimeCompatibilityOnce().then(done);')
        assert browser.execute_script('return window.__inotifyWarningRequests') == 1
        click_visible_selector(browser, '.runtime-compatibility-warning [data-toast-close]')
        wait.until(lambda driver: not driver.find_elements(By.CSS_SELECTOR, '.runtime-compatibility-warning'))
    finally:
        run_fixture_cleanup_phases('inotify warning browser fixture', (
            ('retire-page', lambda: browser.get('about:blank')),
            ('remove-startup-script', lambda: browser.execute_cdp_cmd('Page.removeScriptToEvaluateOnNewDocument', {'identifier': registration})),
        ))
