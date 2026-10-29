# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Native filesystem event delivery through the rendered Finder."""

import json
import shlex
import tempfile
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlencode

import pytest

from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.actions.wheel_input import ScrollOrigin
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401
from tests.tmux_runtime import run_isolated_tmux
from tests.helpers.terminal_url_fixtures import terminal_url_matrix_examples
from tools.product_environment import product_env


def test_native_codex_wrapped_file_context_menu_opens_complete_path(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('codex-wrapped-file')
    request.addfinalizer(lambda: browser.get('about:blank'))
    session = server.tmux.sessions[0]
    browser.get(server.base_url + f'/?sessions={session}&layout=left&tabs=left:{session}')
    WebDriverWait(browser, 15).until(lambda driver: driver.execute_script(
        "return typeof terminalReferenceAtClientPoint === 'function' && terminals.get(arguments[0])?.term?.element && initialAppShellPainted",
        session,
    ))
    with tempfile.TemporaryDirectory(prefix='yfl-', dir='/tmp') as directory:
        path = Path(directory) / 'ssc-resident-cintas-schedule-2026-10-26.md'
        content = 'Wrapped file link reached the complete file.\n'
        path.write_text(content, encoding='utf-8')
        for split in ('ssc-resident-', 'ssc-resident-cintas-'):
            rows = [f'See ({directory}/{split}', f'  {path.name[len(split):]}) to require all units', '  unrelated following prose']
            for row in (1, 2):
                browser.execute_script("selectSession(arguments[0]); terminals.get(arguments[0]).term.yomuxAgentKind='codex'", session)
                # The fixture shell is the only output producer; browser injection races its startup stream.
                output_format = r'\033[2J\033[H\033[3J' + r'%s\r\n' * len(rows)
                command = 'printf ' + ' '.join(shlex.quote(value) for value in [output_format, *rows])
                run_isolated_tmux(server.tmux, 'send-keys', '-t', f'{session}:', command, 'Enter')
                point = browser.execute_async_script(r"""
                  const session=arguments[0],rows=arguments[1],row=arguments[2],done=arguments[arguments.length-1];
                  (async()=>{
                    await selectSession(session);
                    const {term}=terminals.get(session),container=document.getElementById(`term-${session}`);
                    term.yomuxAgentKind='codex';
                    try {
                      await window.__yomuxTestWaitFor(()=>rows.every((text,index)=>
                        term.buffer.active.getLine(term.buffer.active.viewportY+index)?.translateToString(true)===text),{timeoutMs:10000});
                    } catch(error) {
                      return {error:String(error),cols:term.cols,viewportY:term.buffer.active.viewportY,
                        actual:Array.from({length:Math.min(term.buffer.active.length,30)},(_,index)=>term.buffer.active.getLine(index)?.translateToString(true))};
                    }
                    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
                    const screen=terminalScreenElement(container),rect=screen.getBoundingClientRect();
                    const cell=terminalCellDimensions(term,container);
                    const x=row===1?rows[0].indexOf('/')+1:3;
                    const point={x:rect.left+(x-0.5)*cell.width,y:rect.top+(row-0.5)*cell.height};
                    const ref=terminalReferenceAtClientPoint(term,container,point.x,point.y);
                    return {...point,path:ref?.path,cols:term.cols,rows:rows.map((_,i)=>term.buffer.active.getLine(term.buffer.active.viewportY+i).translateToString(true))};
                  })().then(done,error=>done({error:String(error.stack||error)}));
                """, session, rows, row)
                assert 'error' not in point, json.dumps(point)
                assert point['cols'] >= max(map(len, rows)), point
                assert point['path'] == str(path), point
                for event_type in ('mousePressed', 'mouseReleased'):
                    browser.execute_cdp_cmd('Input.dispatchMouseEvent', {
                        'type': event_type, 'x': point['x'], 'y': point['y'], 'button': 'right', 'clickCount': 1,
                    })
                open_button = WebDriverWait(browser, 5).until(lambda driver: next((
                    button for button in driver.find_elements(By.CSS_SELECTOR, '.terminal-context-menu button')
                    if button.text == 'Open file'
                ), None))
                open_button.click()
                WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("""
                  const panel=panelNodes.get(fileEditorItemFor(arguments[0]));
                  return itemIsActivePaneTab(fileEditorItemFor(arguments[0]))
                    && panel?.getBoundingClientRect().height>0
                    && panel?._cmView?.state.doc.toString()===arguments[1];
                """, str(path), content))


@pytest.mark.parametrize('width', [40, 64, 80, 120])
def test_native_tmux_random_url_clipboard_matrix(browser, isolated_dev_server_factory, request, tmp_path, width):
    server = isolated_dev_server_factory(f'url-matrix-{width}')
    (server.paths.config_dir / 'settings.yaml').write_text('general:\n  startup_tips: false\n', encoding='utf-8')
    request.addfinalizer(lambda: browser.get('about:blank'))
    session = server.tmux.sessions[0]
    original_size = browser.get_window_size()
    request.addfinalizer(lambda: browser.set_window_size(original_size['width'], original_size['height']))
    browser.set_window_size(1500, 1900)
    browser.get(server.base_url + f'/?sessions={session}&layout=left&tabs=left:{session}')
    WebDriverWait(browser, 15).until(lambda driver: driver.execute_script(
        "return initialAppShellPainted && terminals.get(arguments[0])?.socket?.readyState === WebSocket.OPEN && document.querySelector('.xterm-rows')",
        session,
    ))
    assert browser.execute_script('return startupHelpersEnabled') is False
    browser.execute_cdp_cmd('Browser.grantPermissions', {
        'origin': server.base_url, 'permissions': ['clipboardReadWrite', 'clipboardSanitizedWrite'],
    })
    fitted = browser.execute_async_script("""
      const session=arguments[0],width=arguments[1],done=arguments[arguments.length-1];
      (async()=>{
        const {term,container}=terminals.get(session),cell=terminalCellDimensions(term,container);
        const style=getComputedStyle(container);
        container.style.setProperty('width',`${Math.ceil(width*cell.width+2+parseFloat(style.paddingLeft)+parseFloat(style.paddingRight))}px`,'important');
        container.style.setProperty('height',`${Math.ceil(80*cell.height+terminalFitBottomReservePx+parseFloat(style.paddingTop)+parseFloat(style.paddingBottom))}px`,'important');
        fitTerminal(session,{force:true,claim:true});
        await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        return {cols:term.cols,rows:term.rows};
      })().then(done,error=>done({error:String(error.stack||error)}));
    """, session, width)
    assert fitted == {'cols':width,'rows':80}, fitted
    for args in [('set-window-option', '-t', session, 'window-size', 'manual'),
                 ('resize-window', '-t', session, '-x', str(width), '-y', '80'),
                 ('send-keys', '-t', session, 'stty -echo; PS1=; PROMPT_COMMAND=', 'Enter')]:
        completed = run_isolated_tmux(server.tmux, *args)
        assert completed.returncode == 0, completed.stderr
    failures, results = [], []
    examples = terminal_url_matrix_examples()
    cases = [(index, expected, 'soft') for index, expected in enumerate(examples)]
    cases.extend((index, examples[index], layout) for index in [0, 13, 59] for layout in ['codex', 'opencode', 'quote'])
    evidence = Path(product_env('E2E_EVIDENCE_DIR', '') or tmp_path)
    evidence.mkdir(parents=True, exist_ok=True)
    report = evidence / f'terminal-url-matrix-{width}.json'
    progress = evidence / f'terminal-url-matrix-{width}.jsonl'
    for index, expected, layout in cases:
        prefix = {'soft': '', 'codex': '    ', 'opencode': '   ', 'quote': '  | '}[layout]
        content_width = width - len(prefix)
        if layout == 'soft':
            printed = [expected]
        else:
            # Repeated OpenCode gutters use an initial URL delimiter to distinguish a wrapped token
            # from a complete URL followed by an unrelated indented word.
            first_width = content_width
            if layout == 'opencode' and len(expected) > content_width:
                first_width = max(position + 1 for position, char in enumerate(expected[:content_width]) if char in '/-.?&=#%')
            printed = [prefix + expected[:first_width]]
            printed.extend(prefix + expected[offset:offset + content_width] for offset in range(first_width, len(expected), content_width))
        marker = f'URL-MATRIX-DONE-{index}-{layout}'
        command = 'printf ' + ' '.join(shlex.quote(value) for value in [
            r'\033[2J\033[H\033[3J' + r'%s\r\n' * (len(printed) + 1), *printed, marker,
        ])
        completed = run_isolated_tmux(server.tmux, 'send-keys', '-t', session, command, 'Enter')
        assert completed.returncode == 0, completed.stderr
        observation = browser.execute_async_script("""
          const session=arguments[0],marker=arguments[1],kind=arguments[2],prefix=arguments[3],done=arguments[arguments.length-1];
          (async()=>{
            const {term}=terminals.get(session);
            term.yomuxAgentKind=kind;
            await window.__yomuxTestWaitFor(()=>Array.from({length:term.rows},(_,index)=>
              term.buffer.active.getLine(term.buffer.active.viewportY+index)?.translateToString(true)||'').some(line=>line===marker),
              {timeoutMs:10000,description:marker});
            await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
            const active=term.buffer.active;
            const lines=Array.from({length:term.rows},(_,index)=>active.getLine(active.viewportY+index)?.translateToString(true)||'');
            const end=lines.indexOf(marker);
            const rows=lines.slice(0,end);
            const container=document.getElementById(`term-${session}`),rect=terminalScreenElement(container).getBoundingClientRect();
            const cell=terminalCellDimensions(term,container);
            const points=[...new Set([0,Math.floor((rows.length-1)/2),rows.length-1])].map(row=>{
              const text=rows[row],indent=text.startsWith(prefix)?prefix.length:(text.match(/^\\s*/)||[''])[0].length;
              const column=row===rows.length-1?text.length-1:indent;
              return {row,column,x:rect.left+(column+0.5)*cell.width,y:rect.top+(row+0.5)*cell.height,
                reference:terminalReferenceAtPosition(term,{x:column+1,y:active.viewportY+row+1})?.href||null};
            });
            const afterColumn=rows.at(-1).length;
            const after=afterColumn<term.cols?{x:afterColumn+1,y:active.viewportY+rows.length}:{x:1,y:active.viewportY+rows.length+1};
            return {cols:term.cols,rows,points,realXterm:term.element.classList.contains('xterm'),
              wrapped:rows.map((_,index)=>active.getLine(active.viewportY+index).isWrapped),
              afterReference:terminalReferenceAtPosition(term,after)?.href||null};
          })().then(done,error=>done({error:String(error.stack||error)}));
        """, session, marker, 'opencode' if layout in ('opencode', 'quote') else 'codex', prefix)
        assert 'error' not in observation, observation
        tmux_width = run_isolated_tmux(server.tmux, 'display-message', '-p', '-t', session, '#{pane_width}').stdout.strip()
        assert observation['realXterm'] and observation['cols'] == width and tmux_width == str(width), observation
        captured = run_isolated_tmux(server.tmux, 'capture-pane', '-p', '-t', session).stdout.splitlines()
        assert captured[:captured.index(marker)] == observation['rows'], {'tmux':captured, 'xterm':observation}
        copied_values = []
        for point in observation['points']:
            point['hit'] = browser.execute_script("""
              const x=arguments[0],y=arguments[1],session=arguments[2],hit=document.elementFromPoint(x,y);
              const {term,container}=terminals.get(session);
              return {tag:hit?.tagName,className:hit?.className,inside:container.contains(hit),
                href:terminalReferenceAtClientPoint(term,container,x,y)?.href||null};
            """, point['x'], point['y'], session)
            assert point['hit']['inside'], {'index':index,'layout':layout,'point':point}
            for event_type in ('mousePressed', 'mouseReleased'):
                browser.execute_cdp_cmd('Input.dispatchMouseEvent', {
                    'type':event_type, 'x':point['x'], 'y':point['y'], 'button':'right', 'clickCount':1,
                })
            copy_button = next((button for button in browser.find_elements(By.CSS_SELECTOR, '.terminal-context-menu button')
                                if button.text == 'Copy URL'), None)
            if copy_button is None:
                copied_values.append({'row':point['row'], 'error':'Copy URL is missing', 'reference':point['reference'],
                                      'labels':[button.text for button in browser.find_elements(By.CSS_SELECTOR, '.terminal-context-menu button')]})
                browser.execute_script('closeTerminalContextMenu()')
                continue
            browser.execute_script('window.__urlMatrixCopyButton=arguments[0]', copy_button)
            copy_button.click()
            copied = browser.execute_async_script("""
              const done=arguments[arguments.length-1];
              window.__yomuxTestWaitFor(()=>window.__urlMatrixCopyButton.dataset.copyFeedbackActive==='true',{timeoutMs:5000})
                .then(()=>navigator.clipboard.readText()).then(done,error=>done({error:String(error)}));
            """)
            copied_values.append({'row':point['row'], 'copied':copied, 'reference':point['reference']})
        result = {'index':index,'width':width,'layout':layout,'expected':expected,**observation,'copies':copied_values}
        results.append(result)
        with progress.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(result) + '\n')
        if observation['afterReference'] is not None or any(value.get('copied') != expected for value in copied_values):
            failures.append(result)
    report.write_text(json.dumps({'seed':20261026,'results':results,'failures':failures}, indent=2), encoding='utf-8')
    print(json.dumps({'width':width,'urls':len(examples),'combinations':len(results),'copies':sum(len(row['copies']) for row in results),
                      'failures':len(failures),'evidence':str(report)}))
    assert not failures, json.dumps({'failed':len(failures),'examples':[
        {'index':row['index'],'layout':row['layout'],'length':len(row['expected']),'rows':len(row['rows']),
         'copies':[{'row':value['row'],'error':value.get('error'),'length':len(value.get('copied','')),
                    'correct':value.get('copied')==row['expected']} for value in row['copies']]}
        for row in failures[:3]],'evidence':str(report)})


def test_native_mixed_notices_scroll_below_editor_controls_on_short_viewports(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('short-notice-stack')
    request.addfinalizer(lambda: browser.get('about:blank'))
    # The test owns explicit tips; automatic startup delivery must not replace its native wheel target.
    (server.paths.config_dir / 'settings.yaml').write_text('general:\n  startup_tips: false\n', encoding='utf-8')
    path = server.paths.home_dir / 'notices.json'
    path.write_text('{"notice":true}\n', encoding='utf-8')
    session = server.tmux.sessions[0]
    browser.get(server.base_url + f'/?sessions={session}&layout=left&tabs=left:{session}')
    setup = browser.execute_async_script("""
      const path=arguments[0],done=arguments[arguments.length-1];
      (async()=>{
        await window.__yomuxTestWaitFor(()=>initialAppShellPainted && clientEventTransportState.connected,{timeoutMs:10000});
        const item=await openFileInEditor(path,'notices.json',{viewMode:'edit',userInitiated:true});
        await window.__yomuxTestWaitFor(()=>panelNodes.get(item)?._cmView,{timeoutMs:10000});
        return {item,automaticTips:startupHelpersEnabled};
      })().then(done,error=>done({error:String(error.stack||error)}));
    """, str(path))
    assert 'error' not in setup, setup
    assert setup['automaticTips'] is False, setup
    original_compact = browser.execute_script("return document.body.classList.contains('app-topbar-touch-compact')")
    try:
        for width, height, usable_height in ((1000, 235, None), (400, 235, None), (1000, 480, 235)):
            browser.execute_cdp_cmd('Emulation.setDeviceMetricsOverride', {'width':width,'height':height,'deviceScaleFactor':1,'mobile':False})
            browser.execute_script('setAppViewportOverride(arguments[0])', {'width':width,'height':usable_height} if usable_height else None)
            for compact in (False, True):
                geometry = browser.execute_async_script("""
                  const item=arguments[0],compact=arguments[1],done=arguments[arguments.length-1];
                  (async()=>{
                    document.body.classList.toggle('app-topbar-touch-compact',compact);
                    await new Promise(requestAnimationFrame);
                    await new Promise(requestAnimationFrame);
                    const enabled=startupHelpersEnabled;startupHelpersEnabled=true;
                    const tip=showStartupHelperTip();startupHelpersEnabled=enabled;
                    // Each geometry case owns a distinct notice, including its rolling dedupe identity.
                    const command=showTimedStatusNotice(`The command could not complete. Please review request ${innerWidth}/${innerHeight}/${compact} and try again.`);
                    window.__shortNoticeIds=[tip,command].map(node=>Number(node.dataset.alertId));
                    const stack=tip.parentElement;
                    const reload=panelNodes.get(item).querySelector('.file-editor-reload-panel');
                    await window.__yomuxTestWaitFor(()=>{
                      const bounds=stack.getBoundingClientRect(),button=reload.getBoundingClientRect();
                      const header=reload.closest('.file-editor-toolbars').getBoundingClientRect();
                      return Math.abs(bounds.top-(Math.ceil(header.bottom)+12))<=1
                        && document.elementFromPoint(button.x+button.width/2,button.y+button.height/2)===reload;
                    },{timeoutMs:5000,description:'notice and header geometry converge after viewport change'});
                    const bounds=stack.getBoundingClientRect(),button=reload.getBoundingClientRect();
                    const body=tip.querySelector('.toast-body').getBoundingClientRect();
                    const bodyHit=document.elementFromPoint(body.left+4,Math.max(body.top,bounds.top)+2);
                    return {top:bounds.top,bottom:bounds.bottom,height:Math.min(innerHeight,appViewport().height),reloadBottom:button.bottom,headerBottom:reload.closest('.file-editor-toolbars').getBoundingClientRect().bottom,
                      hit:document.elementFromPoint(button.x+button.width/2,button.y+button.height/2)===reload,
                      passthrough:!bodyHit?.closest('.attention-alerts'),
                      scrollHeight:stack.scrollHeight,clientHeight:stack.clientHeight,
                      horizontalOverflow:stack.scrollWidth > stack.clientWidth+1,
                      clipped:[tip,command].some(node=>node.scrollHeight > node.clientHeight+1),
                      remaining:Number(command.dataset.toastRemainingMs),
                      tipId:tip.dataset.alertId,commandId:command.dataset.alertId,
                      groups:[...document.querySelectorAll('.dv-groupview')].map(node=>({rect:node.getBoundingClientRect().toJSON(),heads:[...node.querySelectorAll('.panel-head,.file-editor-toolbars')].map(head=>head.getBoundingClientRect().toJSON())})),
                      button:button.toJSON(),width:innerWidth,compact};
                  })().then(done,error=>done({error:String(error.stack||error)}));
                """, setup['item'], compact)
                assert 'error' not in geometry, geometry
                assert geometry['hit'] and geometry['top'] >= geometry['reloadBottom'], json.dumps(geometry)
                assert abs(geometry['top'] - (geometry['headerBottom'] + 12)) <= 1, geometry
                assert geometry['bottom'] <= geometry['height'] - 12 + 1, geometry
                assert geometry['scrollHeight'] > geometry['clientHeight'] and geometry['remaining'] > 0 and not geometry['clipped'] and not geometry['horizontalOverflow'], geometry
                if compact:
                    assert geometry['passthrough'], geometry
                command_keep = browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["commandId"]}"] [data-toast-keep]')
                tip_keep = browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["tipId"]}"] [data-toast-keep]')
                ActionChains(browser).scroll_from_origin(ScrollOrigin.from_element(tip_keep), 0, geometry['scrollHeight']).perform()
                WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return document.querySelector('.attention-alerts').scrollTop > 0"))
                command_keep.click()
                assert 'kept' in browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["commandId"]}"]').get_attribute('class')
                command_close = browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["commandId"]}"] [data-toast-close]')
                command_close.click()
                WebDriverWait(browser, 5).until(lambda driver: not driver.find_elements(By.CSS_SELECTOR, f'[data-alert-id="{geometry["commandId"]}"]'))
                browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["tipId"]}"] [data-toast-keep]').click()
                assert 'kept' in browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["tipId"]}"]').get_attribute('class')
                browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["tipId"]}"] [data-toast-close]').click()
    finally:
        browser.execute_script("""
          for(const id of window.__shortNoticeIds||[]) removeAttentionAlert(id);
          document.body.classList.toggle('app-topbar-touch-compact',arguments[0]);
          setAppViewportOverride(null);
        """, original_compact)
        browser.execute_cdp_cmd('Emulation.clearDeviceMetricsOverride', {})


def test_native_filesystem_root_lists_through_http_and_rendered_finder(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory("native-filesystem-root")
    request.addfinalizer(lambda: browser.get("about:blank"))
    alias = server.paths.workspace_dir / "filesystem-root"
    alias.symlink_to("/", target_is_directory=True)

    def get_terminal(path):
        response = server.request(path)
        if response[0] != 202:
            return response
        receipt = json.loads(response[2])

        def terminal(_server):
            result = _server.request(receipt["operation"]["status_url"])
            return result if result[0] != 202 else False

        return WebDriverWait(server, 5, poll_frequency=0.05).until(terminal)

    for root in ("/", "/./", "/tmp/..", str(alias)):
        for route in ("/api/fs/fast/list", "/api/fs/list", "/api/fs/info"):
            status, _, body = get_terminal(route + "?" + urlencode({"path": root}))
            payload = json.loads(body)
            assert status == 200, (root, route, payload)
            if route.endswith("/info"):
                assert payload["kind"] == "dir" and payload["realpath"] == "/"
            else:
                assert payload["entries"], (root, route, payload)
        status, _, body = get_terminal("/api/fs/read?" + urlencode({"path": root}))
        assert status == 400 and json.loads(body)["error_code"] == "invalid_request", body

    connection = HTTPConnection("127.0.0.1", server.port, timeout=5)
    try:
        connection.request("POST", "/api/fs/batch", headers={"Content-Type": "application/json"}, body=json.dumps({
            "requests": [{"type": kind, "path": "/", "include_repo_info": False} for kind in ("list", "info")],
        }))
        response = connection.getresponse()
        status, body = response.status, response.read()
    finally:
        connection.close()
    if status == 202:
        receipt = json.loads(body)
        status, _, body = get_terminal(receipt["operation"]["status_url"])
    assert status == 200, body
    assert all(item["ok"] and item["status"] == 200 for item in json.loads(body)["responses"]), body

    browser.get(server.base_url + "/?sessions=__finder__&layout=left&tabs=left:__finder__")
    try:
        result = browser.execute_async_script("""
          const done = arguments[arguments.length - 1];
          (async () => {
            await window.__yomuxTestWaitFor(() => typeof openFileExplorerManualRoot === 'function'
              && typeof openFileExplorerPane === 'function', {timeoutMs:10000});
            await openFileExplorerPane();
            await openFileExplorerManualRoot('/');
            await window.__yomuxTestWaitFor(() => currentFileExplorerRoot() === '/'
              && [...document.querySelectorAll('.file-tree-row')].some(row => row.dataset.path === '/usr'),
              {timeoutMs:5000});
            return {root:currentFileExplorerRoot(), visible:[...document.querySelectorAll('.file-tree-row')]
              .some(row => row.dataset.path === '/usr')};
          })().then(done, error => done({error:String(error.stack || error)}));
        """)
        assert result == {"root": "/", "visible": True}, result
    finally:
        browser.get("about:blank")


def test_native_filesystem_events_refresh_visible_finder_and_hidden_finder_editor(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory("native-finder")
    request.addfinalizer(lambda: browser.get("about:blank"))
    directory = server.paths.home_dir / "finder-watch"
    directory.mkdir()
    existing = directory / "existing.json"
    existing.write_text('{"baseline":true}\n', encoding="utf-8")
    created = directory / "created.json"
    browser.get(server.base_url + "/?sessions=__finder__&layout=left&tabs=left:__finder__")
    setup = browser.execute_async_script("""
      const root = arguments[0], existing = arguments[1], done = arguments[arguments.length - 1];
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof openFileExplorerAt === 'function'
          && typeof clientEventTransportState !== 'undefined', {timeoutMs:10000});
        await window.__yomuxTestWaitFor(() => clientEventTransportState.connected, {timeoutMs:10000});
        await openFileExplorerPane();
        await window.__yomuxTestWaitFor(() => fileExplorerTreePaneIsVisible(), {timeoutMs:5000});
        await openFileExplorerManualRoot(root);
        await syncServerWatchRoots({immediate:true});
        await window.__yomuxTestWaitFor(() => serverWatchRootsState.registered
          && serverWatchRootsState.signature.includes(root) && fileExplorerFilesystemWatchToken
          && clientEventTransportState.connected && clientEventTransportState.source
          && [...document.querySelectorAll('.file-tree-row')].some(row => row.dataset.path === existing), {timeoutMs:10000});
        window.__nativeFinderEvents = [];
        window.__nativeFinderObservedSources = new WeakSet();
        window.__observeNativeFinderSource = source => {
          if (window.__nativeFinderObservedSources.has(source)) return;
          window.__nativeFinderObservedSources.add(source);
          source.addEventListener('fs_changed', event => window.__nativeFinderEvents.push(JSON.parse(event.data)));
        };
        window.__observeNativeFinderSource(clientEventTransportState.source);
        return {root:currentFileExplorerRoot(), token:fileExplorerFilesystemWatchToken,
          registered:serverWatchRootsState.registered, connected:clientEventTransportState.connected};
      })().then(done, error => done({error:String(error.stack || error),
        root:typeof currentFileExplorerRoot === 'function' ? currentFileExplorerRoot() : '',
        token:typeof fileExplorerFilesystemWatchToken === 'string' ? fileExplorerFilesystemWatchToken : '',
        registered:typeof serverWatchRootsState === 'object' ? serverWatchRootsState.registered : null,
        rootMode:typeof fileExplorerRootMode === 'string' ? fileExplorerRootMode : '',
        finderVisible:typeof fileExplorerTreePaneIsVisible === 'function' ? fileExplorerTreePaneIsVisible() : null,
        registrationPending:typeof serverWatchRootsState === 'object' ? serverWatchRootsState.registrationPending : null,
        inFlight:typeof serverWatchRootsState === 'object' ? serverWatchRootsState.inFlight : null,
        baselinePromise:typeof serverWatchRootsState === 'object' ? Boolean(serverWatchRootsState.watchBaselinePromise) : null,
        rejectedSignature:typeof serverWatchRootsState === 'object' ? serverWatchRootsState.rejectedSignature : '',
        signature:typeof serverWatchRootsState === 'object' ? serverWatchRootsState.signature : '',
        connected:typeof clientEventTransportState === 'object' ? clientEventTransportState.connected : null,
        apiFailures:typeof jsDebugEvents === 'object' ? jsDebugEvents.filter(event => event.type === 'api'
          && (event.error || event.ok === false)).slice(-5).map(event => ({path:event.endpoint,status:event.status,
            error:event.error,code:event.code})) : [],
        visible:[...document.querySelectorAll('.file-tree-row')].map(row => row.dataset.path)}));
    """, str(directory), str(existing))
    status, _, watch_body = server.request("/api/fs/watch-diff?full=1")
    setup["watchHttpStatus"] = status
    setup["watchResponse"] = json.loads(watch_body)
    evidence = Path(product_env("E2E_EVIDENCE_DIR", "") or server.paths.log_dir) / "native-finder-setup.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(setup, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(setup, sort_keys=True))
    assert "error" not in setup, setup
    observations = []
    try:
        for path, present in ((created, True), (existing, False)):
            before = browser.execute_script("return window.__nativeFinderEvents.length;")
            if present:
                path.write_text('{"created":true}\n', encoding="utf-8")
            else:
                path.unlink()
            result = browser.execute_async_script("""
              const root = arguments[0], path = arguments[1], present = arguments[2], before = arguments[3];
              const done = arguments[arguments.length - 1];
              (async () => {
                await window.__yomuxTestWaitFor(() => window.__nativeFinderEvents.slice(before).some(event =>
                  (event.payload?.paths || []).some(changed => changed === root || changed === path)), {timeoutMs:10000});
                let converged = true;
                try {
                  await window.__yomuxTestWaitFor(() => [...document.querySelectorAll('.file-tree-row')]
                    .some(row => row.dataset.path === path) === present, {timeoutMs:5000});
                } catch (_) { converged = false; }
                return {path, present, converged, token:fileExplorerFilesystemWatchToken,
                  events:window.__nativeFinderEvents.slice(before),
                  visible:[...document.querySelectorAll('.file-tree-row')].map(row => row.dataset.path)};
              })().then(done, error => done({error:String(error.stack || error)}));
            """, str(directory), str(path), present, before)
            assert "error" not in result, result
            observations.append(result)
            evidence.with_name("native-finder-transitions.json").write_text(
                json.dumps(observations, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        editor_setup = browser.execute_async_script("""
          const path = arguments[0], done = arguments[arguments.length - 1];
          (async () => {
            const item = await openFileInEditor(path, {name:'created.json'}, {userInitiated:true});
            removeSessionFromLayout(fileExplorerItemId);
            syncClientEventDemand({immediate:true});
            await window.__yomuxTestWaitFor(() => clientEventTransportState.connected
              && !clientEventTransportState.replacementSource, {timeoutMs:10000});
            await syncServerWatchRoots({immediate:true});
            await window.__yomuxTestWaitFor(() => !fileExplorerTreePaneIsVisible()
              && serverWatchRootsState.registered && JSON.parse(serverWatchRootsState.signature).files.includes(path)
              && fileState.get(path)?.content.includes('"created":true')
              && panelNodes.get(item)?.querySelector('.cm-content'), {timeoutMs:10000});
            window.__nativeFinderEvents = [];
            window.__observeNativeFinderSource(clientEventTransportState.source);
            return {item, content:fileState.get(path)?.content, finderVisible:fileExplorerTreePaneIsVisible(),
              signature:serverWatchRootsState.signature, token:fileExplorerFilesystemWatchToken};
          })().then(done, error => done({error:String(error.stack || error)}));
        """, str(created))
        evidence.with_name("native-editor-setup.json").write_text(
            json.dumps(editor_setup, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        assert "error" not in editor_setup, editor_setup
        browser.execute_script(
            "return panelNodes.get(arguments[0]).querySelector('.cm-content');", editor_setup["item"]
        ).click()
        assert browser.execute_script("return fileEditorPathHasFocus(arguments[0]);", str(created)) is True
        watch_read_start = browser.execute_script("return jsDebugEvents.length;")
        created.write_text('{"external":true,"revision":2}\n', encoding="utf-8")
        editor = browser.execute_async_script("""
          const path = arguments[0], item = arguments[1], before = arguments[2], done = arguments[arguments.length - 1];
          (async () => {
            await window.__yomuxTestWaitFor(() => window.__nativeFinderEvents.some(event =>
              (event.payload?.paths || []).includes(path)), {timeoutMs:10000});
            let converged = true;
            try {
              await window.__yomuxTestWaitFor(() => {
                const state = fileState.get(path), panel = panelNodes.get(item);
                const status = panel?.querySelector('.file-editor-status-message');
                return state?.externalChanged && state?.externalReloadDeferred && fileEditorPathHasFocus(path)
                  && state.content === '{"created":true}\\n'
                  && panel?.querySelector('.cm-content')?.textContent.includes('"created":true')
                  && status?.textContent === t('dialog.externalTitle') && status.getClientRects().length > 0;
              }, {timeoutMs:5000});
            } catch (_) { converged = false; }
            return {converged, finderVisible:fileExplorerTreePaneIsVisible(), content:fileState.get(path)?.content,
              externalChanged:fileState.get(path)?.externalChanged,
              deferred:Boolean(fileState.get(path)?.externalReloadDeferred), focused:fileEditorPathHasFocus(path),
              status:panelNodes.get(item)?.querySelector('.file-editor-status-message')?.textContent,
              watchReadbacks:jsDebugEvents.slice(before).filter(event => event.type === 'api'
                && event.endpoint === '/api/fs/watch-diff').map(event => ({url:event.url,status:event.status,requestId:event.requestId})),
              events:window.__nativeFinderEvents, token:fileExplorerFilesystemWatchToken};
          })().then(done, error => done({error:String(error.stack || error)}));
        """, str(created), editor_setup["item"], watch_read_start)
        evidence.with_name("native-editor-transition.json").write_text(
            json.dumps(editor, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        assert "error" not in editor, editor
        assert editor["watchReadbacks"] and all(row["status"] == 200 and row["requestId"] for row in editor["watchReadbacks"]), editor
        observations.append(editor)
        read_start = browser.execute_script("return jsDebugEvents.length;")
        toast_geometry = browser.execute_script("""
          const item=arguments[0], reload=panelNodes.get(item).querySelector('.file-editor-reload-panel');
          const enabled=startupHelpersEnabled, compact=document.body.classList.contains('app-topbar-touch-compact');
          startupHelpersEnabled=true;
          const tip=showStartupHelperTip();
          startupHelpersEnabled=enabled;
          const stack=tip.parentElement, samples=[];
          const sample=label => {
            const button=reload.getBoundingClientRect(), bounds=stack.getBoundingClientRect();
            samples.push({label,bottom:bounds.bottom,viewport:innerHeight,top:bounds.top,
              reloadBottom:button.bottom,
              hit:document.elementFromPoint(button.x+button.width/2,button.y+button.height/2) === reload});
          };
          try {
            sample('startup');
            const command=showTimedStatusNotice('A command could not complete.');
            sample('command-present');
            document.body.classList.add('app-topbar-touch-compact');
            sample('compact');
            removeAttentionAlert(Number(command.dataset.alertId));
            sample('command-removed');
          } finally {
            document.body.classList.toggle('app-topbar-touch-compact',compact);
          }
          sample('restored');
          return {kind:tip.dataset.toastKind,samples};
        """, editor_setup["item"])
        evidence.with_name("native-editor-toast-geometry.json").write_text(
            json.dumps(toast_geometry, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        assert toast_geometry["kind"] == "startup-helper", toast_geometry
        assert all(sample["hit"] and sample["top"] >= sample["reloadBottom"]
                   and sample["bottom"] <= sample["viewport"] - 12 + 1
                   for sample in toast_geometry["samples"]), toast_geometry
        browser.execute_script(
            "return panelNodes.get(arguments[0]).querySelector('.file-editor-reload-panel');", editor_setup["item"]
        ).click()
        reloaded = browser.execute_async_script("""
          const path = arguments[0], item = arguments[1], before = arguments[2], done = arguments[arguments.length - 1];
          (async () => {
            await window.__yomuxTestWaitFor(() => fileState.get(path)?.content === '{"external":true,"revision":2}\\n'
              && panelNodes.get(item)?.querySelector('.cm-content')?.textContent.includes('"external":true')
              && !fileState.get(path)?.externalChanged, {timeoutMs:5000});
            return {content:fileState.get(path)?.content, finderVisible:fileExplorerTreePaneIsVisible(),
              readbacks:jsDebugEvents.slice(before).filter(event => event.type === 'api' && event.endpoint === '/api/fs/read'
                && new URL(event.url, location.href).searchParams.get('path') === path)
                .map(event => ({url:event.url,status:event.status,requestId:event.requestId}))};
          })().then(done, error => done({error:String(error.stack || error)}));
        """, str(created), editor_setup["item"], read_start)
        evidence.with_name("native-editor-reload.json").write_text(
            json.dumps(reloaded, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        assert "error" not in reloaded, reloaded
        assert reloaded["finderVisible"] is False and reloaded["readbacks"], reloaded
        assert created.read_text(encoding="utf-8") == reloaded["content"]
    finally:
        browser.get("about:blank")
        server.stop()
    assert all(item["converged"] for item in observations), observations
