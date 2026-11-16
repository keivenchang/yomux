# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Trusted Chromium pointer gestures shared by fixture and authenticated journeys."""


from selenium.webdriver.support.ui import WebDriverWait


def settle_native_frames(browser, count=2):
    browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      let remaining = arguments[0];
      const next = () => {if (--remaining <= 0) done(true); else requestAnimationFrame(next);};
      requestAnimationFrame(next);
    """, count)


def cdp_drag_hold(browser, start, end, steps=24, settle=None):
    settle = settle or settle_native_frames
    assert start and end
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": start["x"], "y": start["y"], "button": "left", "buttons": 0, "clickCount": 1})
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": start["x"], "y": start["y"], "button": "none"})
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mousePressed", "x": start["x"], "y": start["y"], "button": "left", "buttons": 1, "clickCount": 1})
    settle(browser)
    for index in range(1, steps + 1):
        x = round(start["x"] + (end["x"] - start["x"]) * index / steps)
        y = round(start["y"] + (end["y"] - start["y"]) * index / steps)
        browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "left", "buttons": 1})
        if index % 4 == 0:
            settle(browser, 1)
    settle(browser, 3)


def cdp_release(browser, point):
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": point["x"], "y": point["y"], "button": "left", "buttons": 0, "clickCount": 1})


def wait_for_visible_selector(browser, selector):
    return WebDriverWait(browser, 5).until(
        lambda driver: _visible_browser_target_point(driver, selector=selector)
    )


def _visible_browser_target_point(browser, *, panel_id=None, selector=None):
    return browser.execute_script(
        """
        const candidates = arguments[0]
          ? [document.getElementById(arguments[0])].filter(Boolean)
          : [...document.querySelectorAll(arguments[1])];
        const node = candidates.find(candidate => {
          const rect = candidate.getBoundingClientRect();
          const style = getComputedStyle(candidate);
          return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
        });
        if (!node) return false;
        if (!arguments[0]) node.scrollIntoView?.({block: 'center', inline: 'center'});
        const rect = node.getBoundingClientRect();
        return {
          x: Math.round(rect.left + rect.width / 2),
          y: Math.round(rect.top + rect.height / 2),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        };
        """,
        panel_id,
        selector,
    )


def _assert_browser_point_hits_target(browser, point, *, panel_id=None, selector=None):
    result = browser.execute_script(
        """
        const panelId = arguments[0];
        const selector = arguments[1];
        const x = arguments[2];
        const y = arguments[3];
        const candidates = panelId
          ? [document.getElementById(panelId)].filter(Boolean)
          : [...document.querySelectorAll(selector || '')];
        const target = candidates.find(candidate => {
          const rect = candidate.getBoundingClientRect();
          const style = getComputedStyle(candidate);
          return rect.width > 0 && rect.height > 0
            && style.display !== 'none' && style.visibility !== 'hidden';
        });
        const hit = document.elementFromPoint(x, y);
        const describe = node => node
          ? `${node.tagName.toLowerCase()}${node.id ? `#${node.id}` : ''}${node.classList?.length ? `.${[...node.classList].join('.')}` : ''}`
          : '<none>';
        return {
          ownsHit: Boolean(target && hit && (target === hit || target.contains(hit))),
          target: describe(target),
          hit: describe(hit),
          x,
          y,
        };
        """,
        panel_id,
        selector,
        point["x"],
        point["y"],
    )
    assert result and result["ownsHit"], f"browser click point is occluded or off-screen: {result}"


def _cdp_click_point(browser, point):
    browser.execute_cdp_cmd(
        "Input.dispatchMouseEvent",
        {
            "type": "mousePressed",
            "x": point["x"],
            "y": point["y"],
            "button": "left",
            "buttons": 1,
            "clickCount": 1,
        },
    )
    browser.execute_cdp_cmd(
        "Input.dispatchMouseEvent",
        {
            "type": "mouseReleased",
            "x": point["x"],
            "y": point["y"],
            "button": "left",
            "buttons": 0,
            "clickCount": 1,
        },
    )


def _click_browser_target(browser, point, *, panel_id=None, selector=None):
    _assert_browser_point_hits_target(browser, point, panel_id=panel_id, selector=selector)
    browser.execute_cdp_cmd(
        "Input.dispatchMouseEvent",
        {"type": "mouseMoved", "x": point["x"], "y": point["y"], "button": "none"},
    )
    # Hover can rebuild or reorder a live tree. Resolve its semantic target
    # after that transition, before committing the trusted press.
    settle_native_frames(browser)
    point = _visible_browser_target_point(browser, panel_id=panel_id, selector=selector)
    assert point, f"browser click target disappeared after hover: {panel_id or selector}"
    _assert_browser_point_hits_target(browser, point, panel_id=panel_id, selector=selector)
    _cdp_click_point(browser, point)


def click_visible_selector(browser, selector):
    point = wait_for_visible_selector(browser, selector)
    _click_browser_target(browser, point, selector=selector)


def terminal_menu_action(driver, point, label):
    for event in ('mousePressed', 'mouseReleased'):
        driver.execute_cdp_cmd('Input.dispatchMouseEvent', {
            'type': event, 'x': point['x'], 'y': point['y'], 'button': 'right', 'clickCount': 1,
        })
    item = driver.execute_script("""
      const buttons=[...document.querySelectorAll('.terminal-context-menu button')];
      const button=buttons.find(node=>node.textContent.startsWith(arguments[0]));
      if(!button)return {labels:buttons.map(node=>node.textContent)};
      const rect=button.getBoundingClientRect(),x=rect.left+rect.width/2,y=rect.top+rect.height/2;
      return {x,y,interactable:button.contains(document.elementFromPoint(x,y))};
    """, label)
    assert item.get('interactable'), {'action':label,'point':point,'item':item}
    for event in ('mousePressed', 'mouseReleased'):
        driver.execute_cdp_cmd('Input.dispatchMouseEvent', {
            'type':event,'x':item['x'],'y':item['y'],'button':'left','clickCount':1,
        })
