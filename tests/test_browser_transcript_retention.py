# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import pytest
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401


@pytest.mark.browser
def test_transcript_delta_retention_includes_system_and_lifecycle_rows(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory("transcript-retention")
    request.addfinalizer(lambda: browser.get("about:blank"))
    browser.get(server.base_url)
    WebDriverWait(browser, 20).until(lambda driver: driver.execute_script(
        "return typeof appendTranscriptItems === 'function' && typeof renderTranscriptItems === 'function' && initialAppShellPainted"
    ))
    rows = browser.execute_script("""
      const preview=document.createElement('div');
      document.body.append(preview);
      const roles=['user','assistant','system','task_started','task_complete'];
      const item=i=>({role:roles[i%roles.length],text:`retention-${i}`});
      renderTranscriptItems(preview,'fixture',Array.from({length:200},(_,i)=>item(i)));
      for(let start=200;start<700;start+=25) {
        appendTranscriptItems(preview,Array.from({length:25},(_,i)=>item(start+i)));
      }
      const result=[...preview.querySelectorAll('.transcript-item')].map(row=>row.querySelector('.transcript-text').textContent);
      preview.remove();
      return result;
    """)
    assert rows == [f"retention-{index}" for index in range(500, 700)]
