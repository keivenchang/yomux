# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

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
