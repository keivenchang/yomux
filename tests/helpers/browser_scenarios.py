"""Reusable browser journeys; tests retain their own assertions and node ids."""
from selenium.webdriver.support.ui import WebDriverWait
from tests.browser_helpers.browser_layout import load_live_runtime_boot_fixture

def terminal_wheel_observation(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return typeof sessionPaneIsAlternateScreen === 'function' && document.querySelector('#term-1 .xterm') !== null && window.__bootSocketInstances.find(item => item.url.includes('/ws?session=1'))?.readyState === WebSocket.OPEN"))
    return browser.execute_async_script("""
      const done = arguments[arguments.length - 1], screen = document.querySelector('#term-1 .xterm');
      const socket = window.__bootSocketInstances.find(item => item.url.includes('/ws?session=1')), forwarded = [];
      screen.addEventListener('wheel', event => { if (event.deltaMode === WheelEvent.DOM_DELTA_LINE) forwarded.push({deltaY:event.deltaY,deltaMode:event.deltaMode}); });
      const state = alternate => ({ok:true,sessions:{'1':{}},windows:[{key:'1:0',session:'1',window_index:'0',active:true,panes:[{window_key:'1:0',session:'1',window_index:'0',pane_index:'0',target:'%11',pane_id:'%11',current_command:alternate?'claude':'bash',active:true,alternate_on:alternate,pid:1234,dead:false}]}]});
      tmuxSignalState=state(true); screen.dispatchEvent(new WheelEvent('wheel',{deltaY:105,deltaMode:0,bubbles:true,cancelable:true})); const mouseForwarded=forwarded.slice();
      for(let i=0;i<5;i++) screen.dispatchEvent(new WheelEvent('wheel',{deltaY:7,deltaMode:0,bubbles:true,cancelable:true})); const touchpadForwarded=forwarded.slice(mouseForwarded.length);
      tmuxSignalState=state(false); const before=forwarded.length; screen.dispatchEvent(new WheelEvent('wheel',{deltaY:105,deltaMode:0,bubbles:true,cancelable:true}));
      setTimeout(()=>done({mouseForwarded,touchpadForwarded,normalForwarded:forwarded.slice(before),tmuxScrollFrames:socket.sent.map(x=>{try{return JSON.parse(x)}catch(_){return null}}).filter(x=>x?.type==='tmux-scroll'),alternateAfterSwitch:sessionPaneIsAlternateScreen('1'),errors:jsDebugFailureEvents('error'),rejections:jsDebugFailureEvents('rejection')}),60);
    """)

def assert_terminal_wheel_observation(metrics):
    assert metrics["mouseForwarded"] == [{"deltaY": 1, "deltaMode": 1}] * 3, metrics
    assert metrics["touchpadForwarded"] == [{"deltaY": 1, "deltaMode": 1}], metrics
    assert metrics["normalForwarded"] == [] and metrics["tmuxScrollFrames"] == [{"type": "tmux-scroll", "direction": "down", "lines": 3}], metrics
    assert metrics["alternateAfterSwitch"] is False and metrics["errors"] == [] and metrics["rejections"] == [], metrics
