# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Required real client rendering and real Chromium terminal menu journeys."""

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
import shlex
import shutil
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer

import pytest
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_console import assert_only_expected_browser_network_error
from tests.browser_helpers.browser_layout import browser, fixture_asset_url, load_live_runtime_boot_fixture  # noqa: F401
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401
from tests.tmux_runtime import run_isolated_tmux
from tests.helpers.terminal_url_fixtures import terminal_url_matrix_examples
from tests.helpers.agent_client_fixtures import write_client_config
from tools.fake_llm_server import Handler
from tools.product_environment import product_env


URLS = [
    'https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Array',
    'https://developer.mozilla.org/en-US/docs/Web/API/Clipboard/writeText',
    'https://www.google.com/search?q=terminal+wrapped+links&source=yomux#results',
    'https://www.google.com/maps/search/?api=1&query=San+Francisco',
    'https://example.com/search?q=terminal%20URL%20wrapping%20regression&mode=expanded&path=%2Fworkspace%2Fstatic_src%2Fjs%2Fyomux%2F99_terminal_boot.js&line=4024&display=plain#terminal-link-detection',
]


def menu_action(driver, point, label):
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



@pytest.mark.real_agent_urls
@pytest.mark.parametrize('client', ['codex', 'claude'])
@pytest.mark.parametrize('width', [int(value) for value in os.environ.get('YOMUX_URL_WIDTHS', '75').split(',')])
def test_real_agent_url_menu(browser, isolated_dev_server_factory, request, tmp_path, monkeypatch, client, width):
    executable = shutil.which(client)
    assert executable, f'real {client} is required'
    version = subprocess.run([executable, '--version'], check=True, capture_output=True, text=True).stdout.strip()
    server = isolated_dev_server_factory(f'real-{client}-urls', env_overrides={
        'YOMUX_CODEX_TRANSCRIPT_ROOT': str(tmp_path / 'client-home' / 'codex-home' / 'sessions'),
        'YOMUX_CLAUDE_TRANSCRIPT_ROOT': str(tmp_path / 'client-home' / 'claude-settings' / 'projects'),
        'YOMUX_OPENCODE_DATABASE': str(tmp_path / 'absent-opencode.db'),
        'YOMUX_STATS_DISABLE_COLLECTORS': '0',
        'YOLOMUX_STATS_DISABLE_COLLECTORS': '0',
        'YOMUX_STATS_SYNTHETIC_HOST': '1',
    })
    request.addfinalizer(lambda: browser.get('about:blank'))
    session = server.tmux.sessions[0]
    urls = URLS + (terminal_url_matrix_examples() if os.environ.get('YOMUX_URL_RANDOM') == '1' else [])
    response = 'URL examples:\n\n' + '\n\n'.join(
        '[' + url.replace('/Global_Objects', '/\nGlobal_Objects').replace('/writeText', '/\nwriteText').replace('&source=', '&\nsource=').replace('&query=', '&\nquery=').replace('example.com/search?', 'example.com/search?\n') + '](' + url + ')'
        if url in URLS else '<' + url + '>' for url in urls) + '\n\nURL_RESPONSE_DONE'
    monkeypatch.setenv('FAKE_LLM_RESPONSE_TEXT', response)

    request_started = threading.Event()
    release_response = threading.Event()
    streaming_started = threading.Event()
    release_stream = threading.Event()

    class HeldCommentaryStream:
        def __init__(self, stream):
            self.stream = stream

        def write(self, data):
            if data.startswith(b'data: {'):
                event = json.loads(data.removeprefix(b'data: '))
                if event['type'] == 'response.output_item.added':
                    # Codex persists a new rollout on its first completed item.
                    # Match the reported ongoing turn before holding its next item.
                    checkpoint = {'id': 'commentary_checkpoint', 'type': 'message', 'role': 'assistant', 'phase': 'commentary',
                                  'content': [{'type': 'output_text', 'text': 'Preparing the requested links.', 'annotations': []}]}
                    for kind, state in (('response.output_item.added', 'in_progress'), ('response.output_item.done', 'completed')):
                        self.stream.write(f'data: {json.dumps({"type": kind, "output_index": 0, "item": {**checkpoint, "status": state}})}\n\n'.encode())
                if 'output_index' in event:
                    event['output_index'] += 1
                if event['type'] in ('response.output_item.added', 'response.output_item.done'):
                    event['item']['phase'] = 'commentary'
                data = f'data: {json.dumps(event)}\n\n'.encode()
                result = self.stream.write(data)
                if event['type'] == 'response.output_text.delta':
                    self.stream.flush()
                    streaming_started.set()
                    if not release_stream.wait(timeout=45):
                        raise TimeoutError('fixture commentary was not released')
                return result
            return self.stream.write(data)

        def flush(self):
            return self.stream.flush()

    class HeldResponseHandler(Handler):
        def do_POST(self):  # noqa: N802 - stdlib handler API
            if urlsplit(self.path).path.endswith(('/responses', '/messages')):
                request_started.set()
                if not release_response.wait(timeout=45):
                    self.send_error(504, 'fixture response was not released')
                    return
            if client == 'codex' and urlsplit(self.path).path.endswith('/responses'):
                original = self.wfile
                self.wfile = HeldCommentaryStream(original)
                try:
                    super().do_POST()
                finally:
                    self.wfile = original
            else:
                super().do_POST()

    fake = ThreadingHTTPServer(('127.0.0.1', 0), HeldResponseHandler)
    thread = threading.Thread(target=fake.serve_forever, daemon=True)
    thread.start()
    request.addfinalizer(lambda: (fake.shutdown(), fake.server_close(), thread.join(timeout=5)))
    request.addfinalizer(release_response.set)
    request.addfinalizer(release_stream.set)
    home = tmp_path / 'client-home'
    home.mkdir()
    config = write_client_config(client, home, fake.server_port, '/scenario/done')
    environment = {'PATH': os.environ['PATH'], 'HOME': str(home), 'TERM': 'xterm-256color', 'LANG': 'C.UTF-8'}
    if client == 'codex':
        environment.update(CODEX_HOME=str(config.parent), OPENAI_API_KEY='fixture-only')
        command = [executable, '--no-alt-screen', '--dangerously-bypass-approvals-and-sandbox', '-C', str(home), 'Show the supplied URL response.']
    else:
        (config.parent / '.claude.json').write_text(json.dumps({'hasCompletedOnboarding': True, 'theme': 'dark', 'projects': {str(home): {'hasTrustDialogAccepted': True}}}))
        environment.update(CLAUDE_CONFIG_DIR=str(config.parent))
        command = [executable, '--bare', '--settings', str(config), '--permission-mode', 'default', 'Show the supplied URL response.']
    old_window = run_isolated_tmux(server.tmux, 'display-message', '-p', '-t', session, '#{window_id}').stdout.strip()
    for args in [('set-option', '-g', 'history-limit', '10000'),
                 ('new-window', '-t', session, '-n', 'url-capture', '-c', str(home), 'bash --noprofile --norc'),
                 ('kill-window', '-t', old_window)]:
        result = run_isolated_tmux(server.tmux, *args)
        assert result.returncode == 0, result.stderr
    browser.set_window_size(1500, 1900)
    browser.get(server.base_url + f'/?sessions={session}&layout=left&tabs=left:{session}')
    WebDriverWait(browser, 20).until(lambda d: d.execute_script(
        'return initialAppShellPainted && terminals.get(arguments[0])?.socket?.readyState === WebSocket.OPEN && document.querySelector(".xterm-rows")', session))
    browser.execute_cdp_cmd('Browser.grantPermissions', {'origin': server.base_url, 'permissions': ['clipboardReadWrite', 'clipboardSanitizedWrite']})
    browser.execute_script('''
      const {term,container}=terminals.get(arguments[0]),cell=terminalCellDimensions(term,container),style=getComputedStyle(container);
      container.style.setProperty('width',`${Math.ceil(arguments[1]*cell.width+2+parseFloat(style.paddingLeft)+parseFloat(style.paddingRight))}px`,'important');
      container.style.setProperty('height',`${Math.ceil(60*cell.height+terminalFitBottomReservePx+parseFloat(style.paddingTop)+parseFloat(style.paddingBottom))}px`,'important');
      fitTerminal(arguments[0],{force:true,claim:true});
    ''', session, width)
    for args in [('set-window-option', '-t', session, 'window-size', 'manual'),
                 ('resize-window', '-t', session, '-x', str(width), '-y', '60')]:
        result = run_isolated_tmux(server.tmux, *args)
        assert result.returncode == 0, result.stderr
    run_isolated_tmux(server.tmux, 'set-window-option', '-t', session, 'remain-on-exit', 'on')
    raw_stream = tmp_path / 'client-stream.ansi'
    run_isolated_tmux(server.tmux, 'pipe-pane', '-t', session, 'cat > ' + shlex.quote(str(raw_stream)))
    launch = ['env', '-i', *[f'{key}={value}' for key, value in environment.items()], *command]
    completed = run_isolated_tmux(server.tmux, 'respawn-pane', '-k', '-t', session, '-c', str(home), shlex.join(launch))
    assert completed.returncode == 0, completed.stderr
    if client == 'codex':
        def stop_private_codex():
            run_isolated_tmux(server.tmux, 'respawn-pane', '-k', '-t', session, 'exec bash --noprofile --norc')
            subprocess.run([executable, 'app-server', 'daemon', 'stop'], env=environment,
                           check=True, capture_output=True, text=True, timeout=30)
        request.addfinalizer(stop_private_codex)
    # Setup prompts are client chrome; only send confirmation inside this private fixture.
    handled_prompts = set()
    working_samples = []
    streaming_shape = None
    evidence = Path(os.environ.get('YOMUX_URL_EVIDENCE') or product_env('E2E_EVIDENCE_DIR') or str(tmp_path))
    evidence.mkdir(parents=True, exist_ok=True)
    report = evidence / f'{client}-{width}.json'

    def response_ready(driver):
        captured = run_isolated_tmux(server.tmux, 'capture-pane', '-p', '-t', session).stdout
        if 'URL_RESPONSE_DONE' in captured:
            return captured
        if 'Yes, I trust this folder' in captured:
            if '❯ No, exit' in captured:
                run_isolated_tmux(server.tmux, 'send-keys', '-t', session, 'Down')
            elif '❯ Yes, I trust' in captured:
                run_isolated_tmux(server.tmux, 'send-keys', '-t', session, 'Enter')
        elif 'Choose the text style' in captured and 'theme' not in handled_prompts:
            handled_prompts.add('theme')
            run_isolated_tmux(server.tmux, 'send-keys', '-t', session, 'Enter')
        return False

    def working_ready(driver):
        response_ready(driver)
        visible = run_isolated_tmux(server.tmux, 'capture-pane', '-p', '-J', '-t', session).stdout
        report.write_text(json.dumps({'version': version, 'requestStarted': request_started.is_set(), 'visible': visible}, indent=2))
        if not request_started.is_set():
            return False
        if 'esc to interrupt' not in visible.lower():
            return False
        status, _, body = server.request('/api/session-metadata?force=1')
        assert status == 200
        agents = json.loads(body)['sessions'].get(session, {}).get('agents', [])
        if len([row for row in agents if row['kind'] == client]) != 1:
            return False
        status, _, body = server.request('/api/stats-agent-status')
        assert status == 200
        if len([row for row in json.loads(body).get('agents', []) if row['kind'] == client]) != 1:
            return False
        return visible

    def check_working_samples(samples):
        observed = []
        assert len(samples) >= 3, samples
        for sample in samples:
            matching = [row for row in sample['agents'] if row['kind'] == client]
            assert len(matching) == 1, sample
            agent = matching[0]
            assert agent['status'] == 'working', sample
            assert all(not agent.get(key) for key in ('working_stopped_ts', 'transition_id', 'cooldown_attention_key')), sample
            persisted = [row for row in sample['roster'] if row['kind'] == client]
            assert len(persisted) == 1 and persisted[0]['state'] == 'run', sample
            assert all(not persisted[0].get(key) for key in ('working_stopped_ts', 'transition_id', 'cooldown_attention_key')), sample
            observed.append(agent)
        assert observed[-1]['status_observed_at'] > observed[0]['status_observed_at'], samples
        assert samples[-1]['roster'][0]['observed_at'] > samples[0]['roster'][0]['observed_at'], samples
        assert samples[-1]['paint']['working'] > 0, samples
        assert samples[-1]['paint']['visible'] and samples[-1]['paint']['session'] == session, samples
        assert all(sample['paint']['completed'] == 0 for sample in samples), samples
        print(json.dumps({'client': client, 'version': version, 'workingSamples': len(samples), 'falseDone': 0}))

    try:
        WebDriverWait(browser, 30, poll_frequency=0.1).until(working_ready)
        started = time.monotonic()

        def sample_working(driver):
            visible = run_isolated_tmux(server.tmux, 'capture-pane', '-p', '-J', '-t', session).stdout
            status, _, body = server.request('/api/session-metadata?force=1')
            assert status == 200
            agents = json.loads(body)['sessions'].get(session, {}).get('agents', [])
            status, _, body = server.request('/api/stats-agent-status')
            assert status == 200
            roster = json.loads(body).get('agents', [])
            paint = driver.execute_script("""
              const row=document.querySelector(`.file-tree-row[data-tabber-session="${CSS.escape(arguments[0])}"]`);
              const rect=row?.getBoundingClientRect();
              return {session:row?.dataset.tabberSession,visible:!!rect&&rect.width>0&&rect.height>0,
                working:row?.querySelectorAll('.agent-window-activity--working').length||0,
                completed:row?.querySelectorAll('.agent-window-activity--cooldown').length||0,
                otherCompletions:[...document.querySelectorAll('.agent-window-activity--cooldown')].map(node=>({
                  owner:node.closest('[id]')?.id,visible:node.getClientRects().length>0}))};
            """, session)
            working_samples.append({'elapsed': time.monotonic() - started, 'visible': visible, 'agents': agents, 'roster': roster, 'paint': paint})
            report.write_text(json.dumps({'version': version, 'workingSamples': working_samples}, indent=2))
            return time.monotonic() - started >= 12

        WebDriverWait(browser, 20, poll_frequency=0.1).until(sample_working)
        check_working_samples(working_samples)
    finally:
        release_response.set()

    if client == 'codex':
        try:
            WebDriverWait(browser, 20, poll_frequency=0.1).until(lambda _: streaming_started.is_set())
            def commentary_ready(_driver):
                text = run_isolated_tmux(server.tmux, 'capture-pane', '-p', '-J', '-t', session).stdout
                report.write_text(json.dumps({'version': version, 'workingSamples': working_samples, 'streamingVisible': text}, indent=2))
                return text if 'developer.mozilla.org' in text or 'Preparing the requested links.' in text else False

            streamed = WebDriverWait(browser, 10, poll_frequency=0.1).until(commentary_ready)
            streaming_shape = {'busyRowVisible': 'esc to interrupt' in streamed.lower(), 'visible': streamed}
            first_streaming_sample = len(working_samples)
            started = time.monotonic()
            WebDriverWait(browser, 20, poll_frequency=0.1).until(sample_working)
            check_working_samples(working_samples[first_streaming_sample:])
        finally:
            release_stream.set()

    try:
        captured = WebDriverWait(browser, 45, poll_frequency=0.2).until(response_ready)
    except Exception:
        (tmp_path / 'failed-pane.ansi').write_text(run_isolated_tmux(server.tmux, 'capture-pane', '-p', '-e', '-t', session).stdout)
        raise
    def completion_ready(driver):
        status, _, body = server.request('/api/session-metadata?force=1')
        assert status == 200
        matching = [row for row in json.loads(body)['sessions'].get(session, {}).get('agents', []) if row['kind'] == client]
        status, _, body = server.request('/api/stats-agent-status')
        assert status == 200
        persisted = [row for row in json.loads(body).get('agents', []) if row['kind'] == client]
        report.write_text(json.dumps({'version': version, 'workingSamples': working_samples, 'completionAgents': matching,
                                     'completionRoster': persisted,
                                     'completionVisible': run_isolated_tmux(server.tmux, 'capture-pane', '-p', '-J', '-t', session).stdout}, indent=2))
        if len(matching) != 1 or matching[0]['status'] not in ('idle', 'cooldown'):
            return False
        if len(persisted) != 1 or persisted[0]['state'] not in ('idle', 'transition') or not persisted[0].get('working_stopped_ts'):
            return False
        assert persisted[0]['transition_id'] and persisted[0]['cooldown_attention_key'], persisted
        return {**matching[0], 'persisted': persisted[0]}

    completion = WebDriverWait(browser, 15, poll_frequency=0.1).until(completion_ready)
    def no_question_marker(driver):
        return driver.execute_script('''
          const roots=[...document.querySelectorAll('[data-pane-tab], [data-tabber-session]')]
            .filter(node=>node.dataset.paneTab===arguments[0]||node.dataset.tabberSession===arguments[0]);
          return roots.length>0 && roots.every(root=>![...root.querySelectorAll('.session-tab-state-marker, .session-tab-queued-question')]
            .some(node=>node.textContent==='ASK'&&node.getClientRects().length>0));
        ''', session)
    WebDriverWait(browser, 15, poll_frequency=0.1).until(no_question_marker)
    joined_ansi = run_isolated_tmux(server.tmux, 'capture-pane', '-p', '-e', '-J', '-S', '-', '-t', session).stdout
    ansi = run_isolated_tmux(server.tmux, 'capture-pane', '-p', '-e', '-S', '-', '-t', session).stdout
    WebDriverWait(browser, 20).until(lambda driver: driver.execute_script('''
      const term=terminals.get(arguments[0]).term;
      return Array.from({length:term.buffer.active.length},(_,i)=>term.buffer.active.getLine(i).translateToString(true)).some(text=>text.includes('URL_RESPONSE_DONE'));
    ''', session))
    observation = browser.execute_script('''
      const {term}=terminals.get(arguments[0]);term.yomuxAgentKind=arguments[1];
      return {cols:term.cols, realXterm:term.element.classList.contains('xterm'), rows:Array.from({length:term.buffer.active.length},(_,i)=>({text:term.buffer.active.getLine(i).translateToString(true),wrapped:term.buffer.active.getLine(i).isWrapped}))};
    ''', session, client)
    capture = {'version': version, 'workingSamples': working_samples, 'streamingShape': streaming_shape, 'completion': completion, 'capture': captured, 'ansi': ansi, 'joinedAnsi': joined_ansi, **observation}
    report.write_text(json.dumps(capture, indent=2))
    print(json.dumps({'client':client,'version':version,'evidence':str(report),'rowCount':len(observation['rows'])}))
    assert observation['cols'] == width and observation['realXterm']
    if width == 75:
        assert any(row['text'].strip() == 'https://example.com/search?' for row in observation['rows']), observation['rows']
        # Keep the reported two-space hard break in the mandatory corpus.
        # A renderer change to soft wraps must not silently remove this case.
        assert any(
            first['text'] == '  https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/'
            and second['text'].startswith('  Global_Objects/Array')
            and not first['wrapped'] and not second['wrapped']
            for first, second in zip(observation['rows'], observation['rows'][1:])
        ), observation['rows']
    # Both real renderers can repaint an idle conversation on resize. The API
    # must keep the decoded conversation date (or explicit unavailable zero
    # when the isolated client has no attributable history), never that output.
    browser.execute_async_script("const done=arguments[0];fetch('/api/session-metadata?force=1').then(r=>r.json()).then(()=>done(true))")
    def conversation_activity(driver):
        value = driver.execute_async_script("""
          const session=arguments[0],done=arguments[1];fetch('/api/activity?scope=all').then(r=>r.json()).then(payload=>{
            const keys=Object.keys(payload.activity).filter(key=>key.startsWith(session+':'));
            done(keys.map(key=>({key,...payload.activity[key]})).find(row=>row.active_recency_ts>=0&&row.active_recency_ts!==row.last_output_ts)||null);
          });
        """, session)
        return value
    activity_before = WebDriverWait(browser, 30).until(conversation_activity)
    # tmux exposes integer output seconds. Cross that boundary before resizing
    # so the readback can prove the renderer actually repainted.
    WebDriverWait(browser, 3, poll_frequency=0.05).until(lambda driver: driver.execute_script(
        'return Date.now()/1000>arguments[0]+1', activity_before['last_output_ts']))
    result = run_isolated_tmux(server.tmux, 'resize-window', '-t', session, '-x', str(width + 1), '-y', '59')
    assert result.returncode == 0, result.stderr
    def repainted_activity(driver):
        value = conversation_activity(driver)
        return value if value and value['last_output_ts'] > activity_before['last_output_ts'] else False
    activity_after = WebDriverWait(browser, 10).until(repainted_activity)
    assert activity_after and activity_after['active_recency_ts'] == activity_before['active_recency_ts'], (activity_before, activity_after)
    capture['conversationActivity'] = {'beforeResize': activity_before, 'afterResize': activity_after}
    # The native page captured the real client. Replays own a separate menu fixture so unrelated
    # long-polling app transports cannot contend with thousands of destination-tab transitions.
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=[session])
    browser.execute_async_script("""
      const done=arguments[arguments.length-1],style=document.createElement('link'),script=document.createElement('script');
      style.rel='stylesheet';style.href=arguments[0];document.head.appendChild(style);
      script.src=arguments[1];script.onload=()=>done(true);script.onerror=()=>done(false);document.head.appendChild(script);
    """, fixture_asset_url('static','vendor','xterm.css'), fixture_asset_url('static','vendor','xterm.js'))
    assert browser.execute_script('return typeof Terminal === "function" && !String(Terminal).includes("FakeTerminal")')
    assert browser.execute_script('return String(window.open).includes("[native code]") && String(navigator.clipboard.readText).includes("[native code]") && String(navigator.clipboard.writeText).includes("[native code]")')
    origin = urlsplit(browser.current_url)
    browser.execute_cdp_cmd('Browser.grantPermissions', {'origin':f'{origin.scheme}://{origin.netloc}','permissions':['clipboardReadWrite','clipboardSanitizedWrite']})
    devtools, connection = browser.start_devtools()
    connection.execute(devtools.page.enable())
    opened_events = []
    callback = connection.add_callback(devtools.page.WindowOpen, opened_events.append)
    request.addfinalizer(lambda: connection.remove_callback(devtools.page.WindowOpen, callback))
    browser.execute_script("""
      const container=document.createElement('div');
      container.className='terminal';Object.assign(container.style,{position:'fixed',left:'100px',top:'80px',width:'1250px',height:'1500px',zIndex:'var(--z-pane-overlay)',background:'#111'});
      document.body.appendChild(container);
      const term=new Terminal({cols:arguments[1],rows:60,scrollback:10000,allowProposedApi:true});
      term.yomuxAgentKind=arguments[2];term.open(container);
      installTerminalContextMenu(arguments[0],term,container);installTerminalLinkProvider(arguments[0],term,container);
      window.__urlReplay={term,container};
    """, session, width, client)
    request.addfinalizer(lambda: browser.execute_script('window.__urlReplay?.term.dispose();window.__urlReplay?.container.remove();delete window.__urlReplay'))
    if os.environ.get('YOMUX_URL_NEGATIVE_CONTROL') == '1':
        baseline = subprocess.run(['git','show','HEAD:static_src/js/yomux/10_core_utils.js'], check=True, capture_output=True, text=True).stdout
        first = baseline.index('function terminalWrappedLineGroup(')
        last = baseline.index('\nfunction ', first + 1)
        browser.execute_script('terminalWrappedLineGroup = ' + baseline[first:last] + '; terminalExplicitHyperlinkReferences = () => [];')
    results = []
    for mode in ['capture', 'text']:
        replay = re.sub(r'\x1b\]8;[^\x07\x1b]*(?:\x07|\x1b\\)', '', joined_ansi) if mode == 'text' else joined_ansi
        browser.execute_async_script(r'''
          const {term}=window.__urlReplay,done=arguments[arguments.length-1];
          term.reset();term.write(arguments[1].replace(/\n/g,'\r\n'),done);
        ''', session, replay)
        for expected in urls:
            points = browser.execute_script('''
              const {term,container}=window.__urlReplay,expected=arguments[1],cell=terminalCellDimensions(term,container),rect=terminalScreenElement(container).getBoundingClientRect();
              const points=[],neighbors=[];let joined='';
              for(let i=0;i<term.buffer.active.length;i++){
                const text=term.buffer.active.getLine(i)?.translateToString(true)||'';
                const token=text.trim().replace(/^[•●⏺]\\s*/, '').split(/\\s+/)[0];
                if(joined===expected)break;
                if(!token || !expected.slice(joined.length).startsWith(token)){
                  joined='';points.length=0;neighbors.length=0;
                  if(!token || !expected.startsWith(token))continue;
                }
                joined+=token;
                const start=text.indexOf(token),end=start+token.length;
                if(start>0 && !text.slice(0,start).trim())neighbors.push(terminalReferenceAtPosition(term,{x:start,y:i+1})?.href||null);
                if(end<term.cols)neighbors.push(terminalReferenceAtPosition(term,{x:end+1,y:i+1})?.href||null);
                for(const column of new Set([text.indexOf(token),text.indexOf(token)+token.length-1]))points.push({row:i,column,x:rect.left+(column+.5)*cell.width,y:rect.top+(i+.5)*cell.height,text});
              }
              if(points.length){term.scrollToLine(points[0].row);for(const point of points){point.y=rect.top+(point.row-term.buffer.active.viewportY+.5)*cell.height;point.explicit=terminalExplicitHyperlinkReferences(term,point.row+1);point.wrapped=term.buffer.active.getLine(point.row).isWrapped;}}
              return {joined,points,neighbors};
            ''', session, expected)
            report.write_text(json.dumps({**capture,'actions':results,'latestPoints':points,'latestExpected':expected,'latestMode':mode},indent=2))
            assert points['joined'] == expected, {'expected':expected,**points}
            assert not any(points['neighbors']), points
            if mode == 'capture' and client == 'codex' and os.environ.get('YOMUX_URL_NEGATIVE_CONTROL') != '1':
                assert any(any(link['href'] == expected for link in point['explicit']) for point in points['points']), points
            if mode == 'text':
                assert all(not point['explicit'] for point in points['points']), points
            if expected not in URLS:
                rows = sorted({point['row'] for point in points['points']})
                chosen = {rows[0], rows[len(rows)//2], rows[-1]}
                points['points'] = [point for index, point in enumerate(points['points']) if point['row'] in chosen and (index == 0 or points['points'][index-1]['row'] != point['row'])]
            for point in points['points']:
                menu_action(browser, point, 'Copy URL')
                copied = browser.execute_async_script('const done=arguments[0];navigator.clipboard.readText().then(done)')
                before = set(browser.window_handles)
                opened_events.clear()
                menu_action(browser, point, 'Open URL')
                WebDriverWait(browser, 10, poll_frequency=0.01).until(lambda d: opened_events and set(d.window_handles) != before)
                opened = opened_events[-1].url
                handle = (set(browser.window_handles) - before).pop()
                browser.execute_cdp_cmd('Target.closeTarget', {'targetId': handle})
                results.append({'mode':mode,'expected':expected,'copied':copied,'opened':opened,'point':point})
                report.write_text(json.dumps({**capture,'actions':results},indent=2))
                assert opened == copied == expected, results[-1]
    blocked_dns = assert_only_expected_browser_network_error(browser, url=tuple({value['opened'] for value in results}), reason='net::ERR_NAME_NOT_RESOLVED', count=None)
    report.write_text(json.dumps({**capture,'actions':results,'blockedDns':blocked_dns},indent=2))
    print(json.dumps({'client':client,'version':version,'openChecks':len(results),'copyChecks':len(results),'failures':0}))


def test_real_xterm_explicit_hyperlink_lifecycle(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('osc8-lifecycle')
    request.addfinalizer(lambda: browser.get('about:blank'))
    session = server.tmux.sessions[0]
    browser.get(server.base_url + f'/?sessions={session}&layout=left&tabs=left:{session}')
    WebDriverWait(browser, 20).until(lambda d: d.execute_script('return initialAppShellPainted && typeof terminalExplicitHyperlinkReferences === "function"'))
    result = browser.execute_async_script(r'''
      const done=arguments[arguments.length-1];
      (async()=>{
        const host=document.createElement('div');document.body.appendChild(host);
        const term=new Terminal({cols:20,rows:4,scrollback:100,allowProposedApi:true});term.open(host);
        const write=text=>new Promise(resolve=>term.write(text,resolve));
        const osc=(url,label)=>`\x1b]8;;${url}\x1b\\${label}\x1b]8;;\x1b\\`;
        const target='https://example.test/complete?query=one%20two#part';
        const failures=[],checks=[];
        const check=(name,x,y,expected)=>{
          const actual=terminalReferenceAtPosition(term,{x,y})?.href||null;
          checks.push({name,x,y,expected,actual});if(actual!==expected)failures.push(checks.at(-1));
        };
        try{
          await write(osc(target,'different label'));
          check('explicit label',1,1,target);check('label end',15,1,target);check('padding',16,1,null);
          check('provider identity',1,1,target);
          const provider=term._core._linkProviderService.linkProviders[0];
          term._core._linkProviderService.linkProviders.unshift({provideLinks(){throw Error('application provider recursively called')}});
          check('unknown provider refused',1,1,null);
          term._core._linkProviderService.linkProviders.shift();
          if(term._core._linkProviderService.linkProviders[0]!==provider)throw Error('provider order changed');
          const originalProvide=provider.provideLinks;
          provider.provideLinks=function(y,callback){queueMicrotask(()=>originalProvide.call(this,y,callback))};
          check('asynchronous provider refused',1,1,null);await Promise.resolve();
          provider.provideLinks=originalProvide;check('synchronous provider restored',1,1,target);
          term.reset();await write('界a'+osc(target,'label'));check('wide-character preceding label',4,1,target);check('wide-character outside label',3,1,null);
          term.reset();await write(osc(target,'one')+osc('https://other.test/','two')+osc(target,'three'));
          check('adjacent first',3,1,target);check('adjacent second',4,1,'https://other.test/');check('repeated target',7,1,target);
          term.reset();for(const chunk of ['\x1b]8;;https://example.test/','fragmented\x1b','\\label','\x1b]8;;\x1b','\\'])await write(chunk);
          check('fragmented escape',5,1,'https://example.test/fragmented');
          await write('\rxxxxx');check('overwrite',1,1,null);
          term.reset();await write(osc(target,'erase'));await write('\r\x1b[2K');check('erase',1,1,null);
          term.reset();await write(osc(target,'abcdefghijklmnopqrstuvwx'));
          check('soft wrap',1,2,target);term.resize(12,4);check('resize reflow',1,2,target);
          await write('\r\n\r\n\r\n\r\n');
          check('scrollback metadata',1,1,target);
          await write('\x1b[?1049h');check('alternate empty',1,1,null);
          await write(osc('https://alternate.test/','alt'));check('alternate target',1,term.buffer.active.cursorY+1,'https://alternate.test/');
          await write('\x1b[?1049l');check('normal restored',1,1,target);
          term.reset();await write(osc('javascript:alert(1)','unsafe'));check('xterm protocol filter',1,1,null);
          term.reset();term.resize(75,4);
          const rows=['  https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/','  Global_Objects/Array'];
          await write(rows.join('\r\n'));term.yomuxAgentKind='codex';
          const full=rows[0].trim()+rows[1].trim();
          check('two-space first',3,1,full);check('two-space continuation',3,2,full);
          check('two-space final',rows[1].length,2,full);check('two-space after',rows[1].length+1,2,null);check('two-space indent',2,2,null);
          term.reset();await write('  https://example.test/path/\r\n  unrelated words here');
          check('prose separated',3,1,'https://example.test/path/');check('prose not linked',3,2,null);
          return {checks,failures};
        }finally{term.dispose();host.remove()}
      })().then(done,error=>done({error:String(error.stack||error)}));
    ''')
    assert 'error' not in result, result
    assert not result['failures'], result['failures']
