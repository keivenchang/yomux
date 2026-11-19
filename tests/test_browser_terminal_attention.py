# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401
from tests.browser_helpers.native_pointer import click_visible_selector
from tests.tmux_runtime import run_isolated_tmux


def attention_settings(browser, color, cursor_color):
    return browser.execute_script('''
          applySettingsPayload({...clientSettingsPayload,mtime_ns:clientSettingsMtimeNs+1e12,
            settings:{...clientSettings,appearance:{...clientSettings.appearance,theme:'dark',
              terminal_theme:'dark',active_color:arguments[0],editor_cursor_color:arguments[1]}}});
          return terminalFocusAppearance();
        ''', color, cursor_color)


def test_terminal_attention_colors_apply_once_and_preserve_application_cursor(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('attention-colors')
    request.addfinalizer(lambda: browser.get('about:blank'))
    session = server.tmux.sessions[0]
    wait = WebDriverWait(browser, 20)

    def tmux(*args):
        result = run_isolated_tmux(server.tmux, *args)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def cursor():
        return tmux('show-options', '-wv', '-t', f'={session}:', 'cursor-colour')

    tmux('set-option', '-t', f'={session}:', 'status', 'on')
    tmux('set-option', '-t', f'={session}:', 'status-left', 'ATTENTION_LEFT')
    tmux('set-option', '-t', f'={session}:', 'status-left-length', '30')
    tmux('set-option', '-t', f'={session}:', 'status-right', 'ATTENTION_RIGHT')
    tmux('set-option', '-t', f'={session}:', 'status-right-length', '30')

    def status_cells_have_background(color):
        # The attach advertises xterm-256color; tmux may encode its nearest
        # palette entry even though xterm also supports true-color output.
        palette_indices = {'#eab308': 178, '#123456': 23}
        return browser.execute_script('''
          const term=terminals.get(arguments[0]).term, buffer=term.buffer.active;
          const line=buffer.getLine(buffer.viewportY+term.rows-1);
          const text=line?.translateToString(true)||'';
          window.__attentionStatusCells={rows:term.rows,text,markers:['ATTENTION_LEFT','ATTENTION_RIGHT'].map(marker=>{
            const index=text.indexOf(marker),cell=index>=0?line.getCell(index):null;
            return {marker,index,rgb:cell?.isBgRGB(),palette:cell?.isBgPalette(),background:cell?.getBgColor()};
          })};
          return ['ATTENTION_LEFT','ATTENTION_RIGHT'].every(marker=>{
            const index=text.indexOf(marker), cell=index>=0?line.getCell(index):null;
            return cell && ((cell.isBgRGB() && cell.getBgColor()===arguments[1])
              || (cell.isBgPalette() && cell.getBgColor()===arguments[2]));
          });
        ''', session, int(color.lstrip('#'), 16), palette_indices[color])

    def wait_for_status_background(color):
        try:
            wait.until(lambda _: status_cells_have_background(color))
        except TimeoutException as error:
            raise AssertionError(browser.execute_script('return window.__attentionStatusCells')) from error

    browser.get(server.base_url + f'/?sessions={session}')
    wait.until(lambda driver: driver.execute_script('''
      return initialAppShellPainted && terminals.get(arguments[0])?.socket?.readyState===WebSocket.OPEN
        && terminals.get(arguments[0])?.term?.element?.classList.contains('xterm');
    ''', session))
    assert browser.execute_script("return !String(Terminal).includes('FakeTerminal')")
    yellow = attention_settings(browser, 'yellow', 'yellow')
    assert yellow['status_bg'] == '#eab308' and yellow['cursor'] == '#ffea00'
    browser.execute_script("scheduleVisibleTerminalResizeAuthority('focus')")
    wait.until(lambda _: cursor() == yellow['cursor'])
    claim = tmux('show-options', '-wv', '-t', f'={session}:', '@yomux_appearance_claim')
    assert yellow['status_bg'] in tmux('display-message', '-p', '-t', f'={session}:', '#{E:status-style}')
    wait_for_status_background(yellow['status_bg'])

    tmux('set-option', '-w', '-t', f'={session}:', 'cursor-colour', '#123456')
    tmux('set-option', '-t', f'={session}:', 'status-style', 'fg=#654321,bg=#123456')
    wait.until(lambda driver: driver.execute_script("return terminals.get(arguments[0]).terminalOutputCursorColor==='#123456'", session))
    changed = attention_settings(browser, 'blue', 'neon-cyan')
    assert changed['status_bg'] == '#3b82f6' and changed['cursor'] == '#66ffff'
    browser.execute_script('''
      const session=arguments[0];
      refreshActiveTerminalCursor();applyTerminalRuntimeSettings({fit:false});
      fitTerminal(session,{force:true});sendRemoteResize(session);refreshTerminal(session);
    ''', session)
    browser.execute_script("""
      const socket=terminals.get(arguments[0]).socket;
      window.__attentionRefreshAck=false;
      window.__attentionRefreshListener=event=>{
        if (typeof event.data !== 'string' || !event.data.startsWith('{')) return;
        const message=JSON.parse(event.data);
        if (message.type==='refresh-ack' && message.txn===781) window.__attentionRefreshAck=true;
      };
      socket.addEventListener('message',window.__attentionRefreshListener);
      socket.send(JSON.stringify({type:'refresh',txn:781}));
    """, session)
    wait.until(lambda driver: driver.execute_script("return window.__attentionRefreshAck"))
    browser.execute_script("terminals.get(arguments[0]).socket.removeEventListener('message',window.__attentionRefreshListener)", session)
    assert cursor() == '#123456'
    assert tmux('show-options', '-wv', '-t', f'={session}:', '@yomux_appearance_claim') == claim
    assert tmux('display-message', '-p', '-t', f'={session}:', '#{E:status-style}') == 'fg=#654321,bg=#123456'
    wait_for_status_background('#123456')
    assert browser.execute_script("return terminals.get(arguments[0]).term._core._themeService.colors.cursor.css", session) == '#123456'

    owner = browser.execute_script("return terminals.get(arguments[0]).resizeOwner", session)
    assert owner and owner['token'] == claim
    browser.execute_script("window.__attentionOldSocket=terminals.get(arguments[0]).socket;window.__attentionOldSocket.close()", session)
    wait.until(lambda driver: driver.execute_script("return terminals.get(arguments[0]).socket!==window.__attentionOldSocket && terminals.get(arguments[0]).socket?.readyState===WebSocket.OPEN", session))
    wait.until(lambda _: 'ignore-size' not in tmux('list-clients', '-F', '#{client_flags}'))
    assert cursor() == '#123456'
    assert tmux('show-options', '-wv', '-t', f'={session}:', '@yomux_appearance_claim') == claim

    blue = browser.execute_script("const value=terminalFocusAppearance();scheduleVisibleTerminalResizeAuthority('refresh');return value")
    wait.until(lambda _: cursor() == blue['cursor'])
    assert tmux('show-options', '-wv', '-t', f'={session}:', '@yomux_appearance_claim') != claim
    # The shell's OSC runs through native tmux and the real xterm parser.
    tmux('send-keys', '-t', f'={session}:', '-l', "printf '\\033]12;#a1b2c3\\007'")
    tmux('send-keys', '-t', f'={session}:', 'Enter')
    wait.until(lambda driver: driver.execute_script("return terminals.get(arguments[0]).terminalOutputCursorColor==='#a1b2c3'", session))
    browser.execute_script('refreshActiveTerminalCursor();applyTerminalRuntimeSettings({fit:false})')
    assert browser.execute_script("return terminals.get(arguments[0]).term._core._themeService.colors.cursor.css", session) == '#a1b2c3'
    browser.execute_script('setBadConnectionCursorState(true);setBadConnectionCursorState(false)')
    assert browser.execute_script("return terminals.get(arguments[0]).term._core._themeService.colors.cursor.css", session) == '#a1b2c3'

    tmux('send-keys', '-t', f'={session}:', '-l', "printf '\\033]112\\007'")
    tmux('send-keys', '-t', f'={session}:', 'Enter')
    wait.until(lambda driver: driver.execute_script("return terminals.get(arguments[0]).terminalOutputCursorColor===arguments[1]", session, blue['cursor']))
    reset_default = browser.execute_script("return terminals.get(arguments[0]).terminalCursorResetDefault", session)
    tmux('set-option', '-w', '-t', f'={session}:', 'cursor-colour', 'default')
    tmux('send-keys', '-t', f'={session}:', '-l', "printf '\\033]12;#a1b2c3\\007'")
    tmux('send-keys', '-t', f'={session}:', 'Enter')
    wait.until(lambda driver: driver.execute_script("return terminals.get(arguments[0]).terminalOutputCursorColor==='#a1b2c3'", session))
    browser.execute_script('refreshActiveTerminalCursor();applyTerminalRuntimeSettings({fit:false})')
    tmux('send-keys', '-t', f'={session}:', '-l', "printf '\\033]112\\007'")
    tmux('send-keys', '-t', f'={session}:', 'Enter')
    wait.until(lambda driver: driver.execute_script("return terminals.get(arguments[0]).term._core._themeService.colors.cursor.css===arguments[1]", session, reset_default))
    browser.execute_script("""
      applySettingsPayload({...clientSettingsPayload,mtime_ns:clientSettingsMtimeNs+1e12,
        settings:{...clientSettings,appearance:{...clientSettings.appearance,theme:'light',terminal_theme:'light'}}});
    """)
    assert browser.execute_script("return terminals.get(arguments[0]).term._core._themeService.colors.cursor.css", session) == reset_default



def test_latest_browser_attention_claim_and_reload(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('attention-two-browsers')
    request.addfinalizer(lambda: browser.get('about:blank'))
    session = server.tmux.sessions[0]
    wait = WebDriverWait(browser, 20)
    original = browser.current_window_handle
    second = None

    def tmux(*args):
        result = run_isolated_tmux(server.tmux, *args)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def ready():
        wait.until(lambda driver: driver.execute_script("return initialAppShellPainted && terminals.get(arguments[0])?.socket?.readyState===WebSocket.OPEN", session))

    def claim_is(color):
        return color['status_bg'] in tmux('display-message', '-p', '-t', f'={session}:', '#{E:status-style}') and tmux('show-options', '-wv', '-t', f'={session}:', 'cursor-colour') == color['cursor']

    try:
        browser.get(server.base_url + f'/?sessions={session}')
        ready()
        yellow = attention_settings(browser, 'yellow', 'yellow')
        browser.execute_script("window.dispatchEvent(new Event('focus'))")
        wait.until(lambda _: claim_is(yellow))
        browser.execute_script("window.open(location.href,'attention-second','width=900,height=700')")
        wait.until(lambda driver: len(driver.window_handles) == 2)
        second = next(handle for handle in browser.window_handles if handle != original)
        browser.switch_to.window(second)
        ready()
        blue = attention_settings(browser, 'blue', 'neon-cyan')
        browser.execute_script("window.dispatchEvent(new Event('focus'))")
        wait.until(lambda _: claim_is(blue))
        blue_cols = browser.execute_script("return terminals.get(arguments[0]).term.cols", session)
        wait.until(lambda _: tmux('display-message', '-p', '-t', f'={session}:', '#{window_width}') == str(blue_cols))
        blue_claim = tmux('show-options', '-wv', '-t', f'={session}:', '@yomux_appearance_claim')
        # The older browser stays in the background while its dimensions change.
        sent = browser.execute_script("""
          return window.opener.eval(`(()=>{
            const session=${JSON.stringify(arguments[0])},item=terminals.get(session),socket=item.socket;
            window.__attentionPassiveAck=false;
            const listener=event=>{
              if(typeof event.data!=='string'||!event.data.startsWith('{'))return;
              const message=JSON.parse(event.data);
              if(message.type==='refresh-ack'&&message.txn===782){
                window.__attentionPassiveAck=true;socket.removeEventListener('message',listener);
              }
            };
            socket.addEventListener('message',listener);
            item.term.resize(180,60);
            const sent=sendRemoteResize(session);
            socket.send(JSON.stringify({type:'refresh',txn:782}));
            return sent;
          })()`);
        """, session)
        assert sent, 'fixture must submit a passive resize from the still-visible older browser'
        wait.until(lambda driver: driver.execute_script('return window.opener.__attentionPassiveAck'))
        assert claim_is(blue)
        assert tmux('display-message', '-p', '-t', f'={session}:', '#{window_width}') == str(blue_cols)
        assert tmux('show-options', '-wv', '-t', f'={session}:', '@yomux_appearance_claim') == blue_claim
        browser.switch_to.window(original)
        click_visible_selector(browser, '#refreshMeta')
        wait.until(lambda _: claim_is(yellow))
        expected = browser.execute_script("return terminals.get(arguments[0]).term.cols", session)
        wait.until(lambda _: tmux('display-message', '-p', '-t', f'={session}:', '#{window_width}') == str(expected))
    finally:
        if second is not None:
            browser.switch_to.window(second)
            browser.get('about:blank')
            browser.close()
        browser.switch_to.window(original)
        browser.get('about:blank')
