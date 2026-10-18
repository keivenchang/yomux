"""Reusable browser journeys; tests retain their own assertions and node ids."""
from selenium.webdriver.support.ui import WebDriverWait
from tests.browser_helpers.browser_layout import load_live_runtime_boot_fixture

def terminal_wheel_observation(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return typeof sessionPaneIsAlternateScreen === 'function' && document.querySelector('#term-1 .xterm') !== null && window.__bootSocketInstances.find(item => item.url.includes('/ws?session=1'))?.readyState === WebSocket.OPEN"))
    return browser.execute_async_script("""
      const done = arguments[arguments.length - 1], screen = document.querySelector('#term-1 .xterm');
      const item = terminals.get('1');
      const socket = window.__bootSocketInstances.find(item => item.url.includes('/ws?session=1'));
      item.term.modes = {mouseTrackingMode:'none'};
      socket.sent.length = 0;
      const state = alternate => ({ok:true,sessions:{'1':{}},windows:[{key:'1:0',session:'1',window_index:'0',active:true,panes:[{window_key:'1:0',session:'1',window_index:'0',pane_index:'0',target:'%11',pane_id:'%11',current_command:alternate?'claude':'bash',active:true,alternate_on:alternate,pid:1234,dead:false}]}]});
      const applicationFrames = () => socket.sent.map(message => JSON.parse(message)).filter(frame => frame.type === 'application-scroll');
      const pagePixels = terminalScrollPageLines(item.term) * terminalWheelPixelLinePx;
      const dispatchTrackpadWheel = deltaY => {
        const event = new WheelEvent('wheel', {deltaY, deltaMode:0, bubbles:true, cancelable:true});
        // The synthetic batch represents continuous motion, never physical-wheel notches.
        Object.defineProperty(event, 'wheelDeltaY', {value:0});
        screen.dispatchEvent(event);
      };
      tmuxSignalState=state(true);
      dispatchTrackpadWheel(pagePixels);
      const framesAfterWholePage = applicationFrames().length;
      const splitPageFrameCounts = [];
      for (const fraction of [1/8, 1/8, 1/4, 1/4, 1/4]) {
        dispatchTrackpadWheel(pagePixels * fraction);
        splitPageFrameCounts.push(applicationFrames().length);
      }
      tmuxSignalState=state(false); screen.dispatchEvent(new WheelEvent('wheel',{deltaY:105,deltaMode:0,bubbles:true,cancelable:true}));
      setTimeout(()=>done({framesAfterWholePage,splitPageFrameCounts,frames:socket.sent.map(x=>{try{return JSON.parse(x)}catch(_){return null}}).filter(x=>x?.type==='application-scroll'||x?.type==='tmux-scroll'),alternateAfterSwitch:sessionPaneIsAlternateScreen('1'),errors:jsDebugFailureEvents('error'),rejections:jsDebugFailureEvents('rejection')}),60);
    """)

def assert_terminal_wheel_observation(metrics):
    assert metrics["framesAfterWholePage"] == 1, metrics
    assert metrics["splitPageFrameCounts"] == [1, 1, 1, 1, 2], metrics
    assert metrics["frames"] == [
        {"type": "application-scroll", "pane": "%11", "direction": "down", "count": 1, "column": 1, "row": 1, "page": True},
        {"type": "application-scroll", "pane": "%11", "direction": "down", "count": 1, "column": 1, "row": 1, "page": True},
        {"type": "tmux-scroll", "direction": "down", "lines": 3},
    ], metrics
    assert metrics["alternateAfterSwitch"] is False and metrics["errors"] == [] and metrics["rejections"] == [], metrics
