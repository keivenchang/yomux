# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


def login_owner(driver, server):
    driver.get(server.base_url + '/login')
    form = WebDriverWait(driver, 15).until(lambda browser: browser.find_element(By.CSS_SELECTOR, 'form[action="/login"]'))
    form.find_element(By.NAME, 'username').send_keys('keivenc')
    form.find_element(By.NAME, 'password').send_keys('random-password')
    form.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()

def _share_json(driver, url, body=None, method=None):
    return driver.execute_async_script("""
      const [url,body,method,done]=arguments;
      const options=body===null ? {method:method || 'GET'} : {method:method || 'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
      fetch(url,options).then(async response=>done({status:response.status,body:await response.json()}),error=>done({error:String(error)}));
    """, url, body, method)


def _button(driver, scope, text):
    return driver.execute_script("""
      const [scope, text] = arguments;
      return [...document.querySelectorAll(scope + ' button')].find(button =>
        button.innerText.trim() === text && button.getClientRects().length &&
        getComputedStyle(button).visibility === 'visible'
      ) || false;
    """, scope, text)


def _workspace_mode(driver, mode):
    # Click only a control the user could click: visible, enabled, and on top at its own center.
    WebDriverWait(driver, 10).until(lambda d: d.execute_script("""
      return [...document.querySelectorAll('.file-editor-panel [data-editor-mode]')].find(node => {
        if (node.dataset.editorMode !== arguments[0] || node.disabled || !node.getClientRects().length) return false;
        if (getComputedStyle(node).visibility !== 'visible') return false;
        const box = node.getBoundingClientRect();
        const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
        return hit === node || node.contains(hit);
      }) || false;
    """, mode)).click()
