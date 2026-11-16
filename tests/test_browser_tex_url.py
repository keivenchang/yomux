# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from urllib.parse import urlsplit

from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401
from tests.browser_helpers.native_pointer import terminal_menu_action


def test_real_xterm_codex_tex_link_before_prose(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('tex-url-wrap')
    request.addfinalizer(lambda: browser.get('about:blank'))
    browser.get(server.base_url + f'/?sessions={server.tmux.sessions[0]}')
    wait = WebDriverWait(browser, 20)
    wait.until(lambda driver: driver.execute_script('return initialAppShellPainted && typeof installTerminalLinkProvider === "function"'))
    origin = urlsplit(browser.current_url)
    browser.execute_cdp_cmd('Browser.grantPermissions', {
        'origin': f'{origin.scheme}://{origin.netloc}',
        'permissions': ['clipboardReadWrite', 'clipboardSanitizedWrite'],
    })
    result = browser.execute_async_script(r'''
      const done=arguments[arguments.length-1];
      (async()=>{
        const rows=[
          'Open the TeX example (https://localhost:7111/?',
          'layout=left&tabs=left:file%3A%2Fhome%2Fkeivenc%2Fdev%2Fyomux%2Fyo7111%2Fexamples%2Ftex%2Fpaper.',
          'tex*) and choose Preview or Split.',
        ];
        const host=document.createElement('div');host.className='terminal';
        host.style.cssText='position:fixed;left:20px;top:120px;width:1200px;height:150px;z-index:var(--z-terminal-badge);background:black';
        document.body.append(host);
        const term=new Terminal({cols:rows[1].length,rows:5,allowProposedApi:true});term.open(host);
        term.yomuxAgentKind='codex';window.__texUrlTerm=term;
        installTerminalLinkProvider('tex-url-wrap',term,host);
        installTerminalContextMenu('tex-url-wrap',term,host);
        window.__texUrlOpened=[];window.open=url=>window.__texUrlOpened.push(url);
        await new Promise(resolve=>term.write(rows.join('\r\n'),resolve));
        await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        const rect=terminalScreenElement(host).getBoundingClientRect(),cell=terminalCellDimensions(term,host);
        const columns=[rows[0].indexOf('https:')+1,1,1];
        return {url:'https://localhost:7111/?'+rows[1]+'tex*',
          real:term.element.classList.contains('xterm')&&!String(Terminal).includes('FakeTerminal'),
          physicalRows:rows.map((_,index)=>term.buffer.active.getLine(index).translateToString(true)),
          points:columns.map((column,index)=>({x:rect.left+(column-0.5)*cell.width,y:rect.top+(index+0.5)*cell.height})),
          links:columns.map((column,index)=>terminalReferenceAtPosition(term,{x:column,y:index+1})?.href||null),
          after:terminalReferenceAtPosition(term,{x:5,y:3})?.href||null};
      })().then(done,error=>done({error:String(error.stack||error)}));
    ''')
    assert 'error' not in result and result['real'], result
    assert result['links'] == [result['url']] * 3, result
    assert result['after'] is None, result
    for point in result['points']:
        terminal_menu_action(browser, point, 'Copy URL')
        copied = browser.execute_async_script('const done=arguments[0];navigator.clipboard.readText().then(done,error=>done({error:String(error)}))')
        assert copied == result['url'], result
        terminal_menu_action(browser, point, 'Open URL')
        assert browser.execute_script('return window.__texUrlOpened.at(-1)') == result['url']
