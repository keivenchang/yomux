# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Prepared history through the real isolated Go server and Chromium."""

import json
import os
import subprocess
import time

import pytest
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.actions.wheel_input import ScrollOrigin
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401
from tests.tmux_runtime import run_isolated_tmux
from yomux_lib.local_services.registry import read_process_cpu_seconds_and_rss


pytestmark = pytest.mark.browser


def test_differ_repo_sha_enter_preserves_comparison_after_clearing_rows(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('differ-refs')
    request.addfinalizer(lambda: browser.get('about:blank'))
    repo = server.paths.home_dir / 'repository'
    repo.mkdir()
    target = repo / 'note.py'
    target.write_text('before\n', encoding='utf-8')
    for args in (['init', '-q', '--initial-branch=main'], ['config', 'user.name', 'Fixture'],
                 ['config', 'user.email', 'fixture@example.invalid'], ['add', 'note.py'], ['commit', '-qm', 'baseline']):
        subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
    baseline = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    target.write_text('after\n', encoding='utf-8')
    subprocess.run(['git', '-C', str(repo), 'commit', '-am', 'changed'], check=True, capture_output=True)
    session = server.tmux.sessions[0]
    run_isolated_tmux(server.tmux, 'respawn-pane', '-k', '-t', f'={session}:', '-c', str(repo), 'bash', '--noprofile', '--norc')
    browser.get(server.base_url + f'/?sessions={session}')
    WebDriverWait(browser, 20).until(lambda driver: driver.execute_script(
        "return typeof fetchSessionFiles === 'function' && typeof applyLayoutSlots === 'function' && document.querySelector('#grid')"))
    setup = browser.execute_async_script("""
      const session=arguments[0],repo=arguments[1],done=arguments[arguments.length-1];
      (async()=>{
        await setTabberMode('advanced');
        const slots=emptyLayoutSlots(); slots[layoutTreeKey]=splitNode('row',leafNode('left'),leafNode('right'));
        slots.left=paneStateWithTabs([differItemId],differItemId);slots.right=paneStateWithTabs([session],session);
        applyLayoutSlots(slots);fileExplorerChangesSelectedSession=session;
        diffRefFrom='HEAD';diffRefTo='current';diffRefsByRepo={};
        await fetchSessionFiles({destination:'differ',session,force:true,freshGit:true,silent:true});
        await window.__yomuxTestWaitFor(()=>[...document.querySelectorAll('[data-diff-ref-controls]')]
          .some(node=>node.dataset.diffRefRepo===repo && node.querySelector('[data-diff-ref-from]')),{timeoutMs:10000});
        const originalFetch=window.fetch;window.__comparisonUrls=[];
        window.fetch=(url,...args)=>{
          if(String(url).includes('/api/session-files'))window.__comparisonUrls.push(String(url));
          return originalFetch(url,...args);
        };
        return fileExplorerSessionFilesState.payload.files.filter(file=>file.repo===repo && sessionFileIsDifferVisible(file)).length;
      })().then(done,error=>done({error:String(error.stack||error),
        payload:fileExplorerSessionFilesState.payload,visible:fileExplorerSessionFilesPaneIsVisible()}));
    """, session, str(repo))
    assert setup == 0, setup
    field = browser.execute_script("""
      return [...document.querySelectorAll('[data-diff-ref-controls]')].find(node=>node.dataset.diffRefRepo===arguments[0])
        .querySelector('[data-diff-ref-from]');
    """, str(repo))
    field.click()
    modifier = Keys.COMMAND if browser.capabilities['platformName'].lower() in {'mac', 'macos', 'darwin'} else Keys.CONTROL
    field.send_keys(modifier, 'a')
    field.send_keys(baseline[:7])
    field.send_keys(Keys.ENTER)
    result = browser.execute_async_script("""
      const repo=arguments[0],baseline=arguments[1],done=arguments[arguments.length-1];
      (async()=>{
        await window.__yomuxTestWaitFor(()=>{
          const payload=fileExplorerSessionFilesState.payload;
          const row=payload.repos.find(row=>row.repo===repo);
          return !sessionFilesRequestIsActiveForDestination('differ') && row?.comparison_from_ref
            && baseline.startsWith(row.comparison_from_ref)
            && payload.files.some(file=>file.repo===repo && file.path==='note.py');
        },{timeoutMs:15000});
        return {urls:window.__comparisonUrls,rows:document.querySelectorAll('.file-explorer-changes-panel .file-tree-row').length,
          repo:fileExplorerSessionFilesState.payload.repos.find(row=>row.repo===repo)};
      })().then(done,error=>done({error:String(error.stack||error),urls:window.__comparisonUrls}));
    """, str(repo), baseline)
    assert 'error' not in result, result
    assert result['rows'] > 0 and result['repo']['count'] == 1, result
    for url in result['urls']:
        query = parse_qs(urlsplit(url).query)
        refs = json.loads(query['refs'][0])
        assert baseline.startswith(refs[str(repo)]['from']) and refs[str(repo)]['to'] == 'current', result


def test_differ_home_history_preparation_and_cached_refresh(browser, isolated_dev_server_factory, tmp_path, request):
    home = tmp_path / 'runtime-1-differ-history' / 'home'
    archive = home / '.codex' / 'sessions'
    archive.mkdir(parents=True)
    repositories = [home / 'alpha', home / 'beta']
    records = []
    for repository in repositories:
        repository.mkdir()
        (repository / 'note.py').write_bytes(b'initial\n')
        for args in (['init', '-q', '--initial-branch=main'], ['config', 'user.name', 'Fixture'],
                     ['config', 'user.email', 'fixture@example.invalid'], ['add', 'note.py'], ['commit', '-qm', 'initial']):
            subprocess.run(['git', '-C', str(repository), *args], check=True, capture_output=True)
        (repository / 'note.py').write_bytes(b'modified\n')
        records.append({'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'exec_command',
                        'arguments': {'cmd': 'git add note.py', 'workdir': str(repository)}}})
    transcript = archive / 'rollout-reference.jsonl'
    transcript.write_bytes(b''.join(json.dumps(record).encode() + b'\n' for record in records)
                           + b'{"type":"progress"}\n' * 12000)
    server = isolated_dev_server_factory('differ-history', env_overrides={'YOMUX_CODEX_TRANSCRIPT_ROOT': str(archive)})
    request.addfinalizer(lambda: browser.get('about:blank'))
    session = server.tmux.sessions[0]
    run_isolated_tmux(server.tmux, 'rename-session', '-t', session, '1')
    server.tmux.sessions[:] = ['1']
    run_isolated_tmux(server.tmux, 'respawn-pane', '-k', '-t', '=1:', '-c', str(home), 'bash', '--noprofile', '--norc')
    assert not (home / '.git').exists()
    assert not list(server.paths.state_dir.rglob('view-*.json'))
    browser.get(server.base_url + '/?sessions=1')
    WebDriverWait(browser, 20).until(lambda driver: driver.execute_script(
        "return typeof fetchSessionFiles === 'function' && typeof applyLayoutSlots === 'function' && document.querySelector('#grid') && terminals.get('1')?.socket?.readyState === WebSocket.OPEN"))
    resources_before = read_process_cpu_seconds_and_rss(server.process.pid)
    assert resources_before is not None
    measurement_started = time.monotonic()
    metrics = browser.execute_async_script("""
      const done=arguments[arguments.length-1];
      (async()=>{
        await setTabberMode('advanced');
        const slots=emptyLayoutSlots();
        slots[layoutTreeKey]=splitNode('row',leafNode('left'),leafNode('right'));
        slots.left=paneStateWithTabs([differItemId],differItemId);
        slots.right=paneStateWithTabs(['1'],'1');
        applyLayoutSlots(slots);
        fileExplorerChangesSelectedSession='1';
        await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        const started=performance.now();
        await fetchSessionFiles({destination:'differ',session:'1',force:true,silent:true});
        const elapsed=performance.now()-started;
        const pingStarted=performance.now();
        const ping=fetch('/api/ping').then(response=>{if(!response.ok)throw new Error('ping failed');return performance.now()-pingStarted;});
        const terminal=terminals.get('1');
        const terminalStarted=performance.now();
        const terminalLatency=new Promise((resolve,reject)=>{
          let output='';
          const timeout=setTimeout(()=>{terminal.socket.removeEventListener('message',receive);reject(new Error('terminal response timeout'));},5000);
          function receive(event){
            output+=typeof event.data === 'string' ? event.data : new TextDecoder().decode(event.data);
            if(output.includes('DIFFERPONG')){clearTimeout(timeout);terminal.socket.removeEventListener('message',receive);resolve(performance.now()-terminalStarted);}
          }
          terminal.socket.addEventListener('message',receive);
          terminal.socket.send(JSON.stringify({type:'input',data:'printf "DIFFER%s" PONG\\r'}));
        });
        const [ping_ms,terminal_ms]=await Promise.all([ping,terminalLatency]);
        return {elapsed,ping_ms,terminal_ms,preparation:fileExplorerSessionFilesState.payload.preparation,
          repos:fileExplorerSessionFilesState.payload.repos.map(repo=>repo.repo),
          visible:fileExplorerSessionFilesPaneIsVisible(),text:document.querySelector('.file-explorer-changes-panel')?.textContent||''};
      })().then(done,error=>done({error:String(error.stack||error)}));
    """)
    print(json.dumps({'initial': metrics}))
    assert 'error' not in metrics, metrics
    assert metrics['elapsed'] < 250, metrics
    assert metrics['preparation']['partial'] is True, metrics
    assert 'Indexing history' in metrics['text'], metrics
    WebDriverWait(browser, 30).until(lambda driver: driver.execute_script(
        "return fileExplorerSessionFilesState.payload.preparation?.partial === false && fileExplorerSessionFilesState.payload.repos.length === 2"
        " && !sessionFilesRequestIsActiveForDestination('differ')"
        " && fileTreeRenderOwners.get(document.querySelector('.file-explorer-changes-panel .changes-groups'))?.completed === true"))
    progress_render = browser.execute_async_script("""
      const done=arguments[arguments.length-1];
      (async()=>{
        const originalApiFetch=apiFetch;
        const state=fileExplorerSessionFilesState;
        const root=document.querySelector('.file-explorer-changes-panel');
        let originalPayload=state.payload;
        const originalCompletionRefresh=refreshSessionFilesCompletionSurfaces;
        let observer;
        try{
        // Synthetic stream transactions own their responses; a real producer's
        // late completion must not replace the deliberately partial fixture.
        refreshSessionFilesCompletionSurfaces=async()=>false;
        await Promise.allSettled([...sessionFilesCompletionRevalidations.values()].map(record=>record.promise));
        originalPayload=state.payload;
        const originalPreparation=originalPayload.preparation;
        const preparation={...originalPreparation,partial:true,error:'index /fixture/rollout.jsonl: transient read failure'};
        setSessionFilesPayloadForDestination('differ',{...state.payload,preparation});
        state.signature=sessionFilesPayloadSignatureForPayload(state.payload);
        renderFileExplorerChangesPanels({view:'differ',force:true});
        const paint=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        const deadline=performance.now()+5000;
        while(!root.querySelector('.changes-summary-label')?.textContent.includes('Indexing history')
          || fileTreeRenderOwners.get(root.querySelector('.changes-groups'))?.completed!==true){
          if(performance.now()>deadline)throw new Error('partial summary did not finish its render '+JSON.stringify({text:root.textContent,preparation:state.payload.preparation,owner:fileTreeRenderOwners.get(root.querySelector('.changes-groups')),connected:root.isConnected}));
          await paint();
        }
        const baseline=JSON.parse(JSON.stringify(state.payload));
        const summary=root.querySelector('.changes-comparison-summary');
        const summaryText=summary.textContent;
        const rows=[...root.querySelectorAll('.file-tree-row')];
        const groups=root.querySelector('.changes-groups');
        const snapshots=[];
        const removed=[];
        let treeWrites=0,summaryWrites=0;
        observer=new MutationObserver(records=>{
          for(const record of records){
            if(record.target.closest?.('.changes-file-list'))treeWrites++;
            if(record.target===summary||summary.contains(record.target))summaryWrites++;
            for(const node of record.removedNodes){
              if(node===summary||node.contains?.(summary)||rows.some(row=>node===row||node.contains?.(row)))removed.push(node.nodeName);
            }
          }
        });
        observer.observe(root,{childList:true,subtree:true});
        let controller;
        apiFetch=(url,...args)=>String(url).startsWith('/api/session-files-stream?')
          ? Promise.resolve(new Response(new ReadableStream({start(value){controller=value;}}),{headers:{'Content-Type':'text/event-stream'}}))
          : originalApiFetch(url,...args);
        const emit=(event,data)=>controller.enqueue(new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
        const sample=async phase=>{
          await paint();
          snapshots.push({phase,text:root.querySelector('.changes-comparison-summary')?.textContent,
            sameSummary:root.querySelector('.changes-comparison-summary')===summary,
            sameRows:rows.every(row=>row.isConnected),loading:!!root.querySelector('.changes-repo-inline-loading')});
        };
          for(let cycle=0;cycle<3;cycle++){
            const pending=fetchSessionFiles({destination:'differ',session:'1',force:true,freshGit:true,background:cycle!==1,silent:true});
            await paint();
            emit('start',{session:'1',preparation:{...preparation,bytes_read:cycle+1}});
            await sample(`${cycle}:start`);
            for(const repo of baseline.repos){
              emit('repo',{session:'1',preparation,repos:[repo],files:[],attribution_complete:false,
                refs_by_repo:baseline.refs_by_repo,from_ref:baseline.from_ref,to_ref:baseline.to_ref});
              await sample(`${cycle}:early-repo`);
              emit('repo',{session:'1',preparation,repos:[repo],files:baseline.files.filter(file=>file.repo===repo.repo),attribution_complete:true,
                refs_by_repo:baseline.refs_by_repo,from_ref:baseline.from_ref,to_ref:baseline.to_ref});
              await sample(`${cycle}:repo`);
            }
            emit('done',{...baseline,preparation:{...preparation,records_indexed:cycle+1},repo_paths:baseline.repos.map(repo=>repo.repo)});
            controller.close();
            await pending;
            await sample(`${cycle}:done`);
          }
          observer.disconnect();
          // A repository's attribution query can finish while history is still
          // partial. Its absent cached rows remain visible until complete data.
          const retainedRepo=baseline.repos[0].repo;
          const retainedInfo={...baseline.repos[0],count:2,added:7,removed:4};
          const fixtureFile=path=>({...baseline.files[0],repo:retainedRepo,path,abs_path:`${retainedRepo}/${path}`,status:'M'});
          const savedOnly=fixtureFile('saved-only.py'),kept=fixtureFile('kept.py'),fresh=fixtureFile('fresh.py');
          const seeded={...baseline,repos:[retainedInfo],files:[savedOnly,kept],preparation};
          setSessionFilesPayloadForDestination('differ',seeded);
          state.signature=sessionFilesPayloadSignatureForPayload(state.payload);
          renderFileExplorerChangesPanels({view:'differ',force:true});
          const settled=async()=>{
            await window.__yomuxTestWaitFor(()=>fileTreeRenderOwners.get(root.querySelector('.changes-groups'))?.completed===true,{timeoutMs:5000});
            await paint();
          };
          await settled();
          const retainedSnapshots=[];
          for(const partial of [true,false]){
            const pending=fetchSessionFiles({destination:'differ',session:'1',force:true,background:true,silent:true});
            await paint();
            const currentPreparation={...preparation,partial};
            emit('start',{session:'1',preparation:currentPreparation});
            emit('repo',{session:'1',preparation:currentPreparation,repos:[retainedInfo],files:[kept,fresh],
              attribution_complete:true,refs_by_repo:baseline.refs_by_repo,from_ref:baseline.from_ref,to_ref:baseline.to_ref});
            emit('done',{...seeded,files:[kept,fresh],preparation:currentPreparation,repo_paths:[retainedRepo]});
            controller.close();await pending;await settled();
            const section=[...root.querySelectorAll('.changes-repo-group')].find(node=>node.dataset.changesRepo===retainedRepo);
            retainedSnapshots.push({partial,paths:[...section.querySelectorAll('.file-tree-row[data-kind="file"]')].map(row=>row.dataset.path).sort(),
              count:section.querySelector('.changes-repo-count')?.textContent,
              totals:section.querySelector('.changes-repo-totals')?.textContent});
          }
          setSessionFilesPayloadForDestination('differ',baseline);
          state.signature=sessionFilesPayloadSignatureForPayload(state.payload);
          renderFileExplorerChangesPanels({view:'differ',force:true});await settled();
          const firstRepo=baseline.repos[0].repo;
          const added={...baseline.files[0],repo:firstRepo,path:'converged.py',abs_path:`${firstRepo}/converged.py`};
          const pending=fetchSessionFiles({destination:'differ',session:'1',force:true,background:true,silent:true});
          await paint();
          emit('start',{session:'1',preparation});
          for(const repo of baseline.repos){
            emit('repo',{session:'1',repos:[repo],files:[...baseline.files.filter(file=>file.repo===repo.repo),...(repo.repo===firstRepo?[added]:[])],
              refs_by_repo:baseline.refs_by_repo,from_ref:baseline.from_ref,to_ref:baseline.to_ref});
          }
          emit('done',{...baseline,preparation:{...originalPreparation,partial:false},repo_paths:baseline.repos.map(repo=>repo.repo)});
          controller.close();await pending;
          const convergeDeadline=performance.now()+5000;
          while(![...root.querySelectorAll('.file-tree-row')].some(row=>row.dataset.path===added.abs_path)){
            if(performance.now()>convergeDeadline)throw new Error('changed streamed file did not converge');
            await paint();
          }
          return {summaryText,snapshots,removed,treeWrites,summaryWrites,retainedRepo,retainedSnapshots,converged:state.payload.preparation.partial===false};
        }finally{
          observer?.disconnect();apiFetch=originalApiFetch;
          refreshSessionFilesCompletionSurfaces=originalCompletionRefresh;
          setSessionFilesPayloadForDestination('differ',originalPayload);
          state.signature=sessionFilesPayloadSignatureForPayload(originalPayload);
          renderFileExplorerChangesPanels({view:'differ'});
        }
      })().then(done,error=>done({error:String(error.stack||error)}));
    """)
    print(json.dumps({'stream_refresh': progress_render}))
    assert 'error' not in progress_render, progress_render
    assert not progress_render['removed'], progress_render
    assert progress_render['treeWrites'] == progress_render['summaryWrites'] == 0, progress_render
    assert progress_render['converged'] is True, progress_render
    retained_repo = progress_render['retainedRepo']
    partial_rows, complete_rows = progress_render['retainedSnapshots']
    assert partial_rows['paths'] == sorted(f'{retained_repo}/{name}' for name in ['saved-only.py', 'kept.py', 'fresh.py']), progress_render
    assert complete_rows['paths'] == sorted(f'{retained_repo}/{name}' for name in ['kept.py', 'fresh.py']), progress_render
    assert partial_rows['count'] == '3' and complete_rows['count'] == '2', progress_render
    assert '+7' in partial_rows['totals'] and '-4' in partial_rows['totals'], progress_render
    assert all(sample['text'] == progress_render['summaryText'] and sample['sameSummary']
               and sample['sameRows'] and (sample['phase'].startswith('1:') or not sample['loading'])
               for sample in progress_render['snapshots']), progress_render
    resources_after = read_process_cpu_seconds_and_rss(server.process.pid)
    assert resources_after is not None
    resource = {'cpu_seconds': resources_after[0] - resources_before[0],
                'rss_bytes': resources_after[1], 'observed_seconds': time.monotonic() - measurement_started}
    result = browser.execute_async_script("""
      const done=arguments[arguments.length-1];
      (async()=>{
        const before={loaded:fileExplorerSessionFilesState.payload.loaded,streaming:fileExplorerSessionFilesState.payload.streaming,key:fileExplorerSessionFilesState.payload.request_key,request:sessionFilesRequestForDestination('differ','1')};
        const prior=fileExplorerSessionFilesState.payload.files.map(file=>file.abs_path);
        const started=performance.now();
        const pending=fetchSessionFiles({destination:'differ',session:'1',freshGit:true,force:true,silent:true,background:true});
        const immediate=fileExplorerSessionFilesState.payload.files.map(file=>file.abs_path);
        await pending;
        return {elapsed:performance.now()-started,before,prior,immediate,final:fileExplorerSessionFilesState.payload.files.map(file=>file.abs_path),
          preparation:fileExplorerSessionFilesState.payload.preparation};
      })().then(done,error=>done({error:String(error.stack||error)}));
    """)
    print(json.dumps({'refresh': result}))
    assert 'error' not in result, result
    assert result['elapsed'] < 250, result
    assert set(result['prior']) == set(result['immediate']) == set(result['final']) == {str(repo / 'note.py') for repo in repositories}
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script(
        "return arguments[0].every(path => [...document.querySelectorAll('.file-explorer-changes-panel .file-tree-row')].some(row => row.dataset.path === path))",
        result['final']))
    connection = HTTPConnection('127.0.0.1', server.port, timeout=5)
    started = time.monotonic()
    connection.request('GET', '/api/ping')
    response = connection.getresponse()
    response.read()
    ping_ms = (time.monotonic() - started) * 1000
    connection.close()
    assert response.status == 200
    print(json.dumps({'cold_view_ms': metrics['elapsed'], 'catchup': result['preparation'], 'idle_ping_ms': ping_ms, 'indexing_ping_ms': metrics['ping_ms'], 'indexing_terminal_ms': metrics['terminal_ms'], 'background_resource': resource, 'prepared_view_ms': result['elapsed']}))


@pytest.mark.parametrize('refresh', ['manual', 'automatic'])
def test_repo_diff_native_finder_open_preserves_scrolled_reading_during_refresh(
    browser, isolated_dev_server_factory, tmp_path, request, refresh,
):
    server = isolated_dev_server_factory('repo-diff-reading')
    (server.paths.config_dir / 'settings.yaml').write_text(
        'general:\n  default_tabber_mode: advanced\n  startup_tips: false\n', encoding='utf-8',
    )
    request.addfinalizer(lambda: browser.get('about:blank'))
    repository = server.paths.home_dir / 'reading-repository'
    repository.mkdir()
    for index in range(64):
        path = repository / ('src' if index < 32 else 'docs') / f'file-{index:02}.go'
        path.parent.mkdir(exist_ok=True)
        path.write_text('baseline\n', encoding='utf-8')

    def git(*args, **kwargs):
        return subprocess.run(['git', '-C', str(repository), *args], check=True, capture_output=True, **kwargs)

    git('init', '-q', '--initial-branch=main')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    git('add', '.')
    git('commit', '-qm', 'baseline')
    for path in repository.glob('*/*.go'):
        path.write_text('committed change\n', encoding='utf-8')
    message = 'Release reading context\n\n' + '\n'.join(
        f'Release story line {index}: preserve the workspace and every scroll position.' for index in range(100)
    )
    git('commit', '-am', message)
    head = git('rev-parse', 'HEAD', text=True).stdout.strip()
    dirty = repository / 'src' / 'file-00.go'
    dirty.write_text('first dirty line\n', encoding='utf-8')
    session = server.tmux.sessions[0]
    run_isolated_tmux(server.tmux, 'respawn-pane', '-k', '-t', f'={session}:',
                      '-c', str(server.paths.home_dir), 'bash', '--noprofile', '--norc')
    browser.get(server.base_url + f'/?sessions=files,{session}&layout=row@50(left,right)&tabs=left:files;right:{session}')
    wait = WebDriverWait(browser, 20)
    root_input = wait.until(lambda driver: next((node for node in driver.find_elements(
        By.CSS_SELECTOR, '.file-explorer-path-inline',
    ) if node.is_displayed()), None))
    modifier = Keys.COMMAND if browser.capabilities['platformName'].lower() in {'mac', 'macos', 'darwin'} else Keys.CONTROL
    root_input.send_keys(modifier, 'a')
    root_input.send_keys(str(server.paths.home_dir), Keys.ENTER)
    finder_row = wait.until(lambda driver: next((node for node in driver.find_elements(
        By.CSS_SELECTOR, f'.file-explorer-panel[data-file-explorer-view="finder"] .file-tree-row[data-path="{repository}"]'
    ) if node.is_displayed()), None))
    browser.execute_script("""
      const p=window.__nativeRepoReading={fetch:window.fetch,hold:false,pending:[],responses:[]};
      window.fetch=(input,options)=>{
        const url=new URL(String(input),location.href),response=p.fetch.call(window,input,options);
        if(!['/api/fs/git-history','/api/fs/git-commit'].includes(url.pathname))return response;
        return response.then(async original=>{
          const data=await original.clone().json(),kind=url.pathname==='/api/fs/git-commit'?'detail':url.searchParams.get('worktree');
          const record={kind,url:url.href,status:original.status,data};p.responses.push(record);
          if(p.hold&&url.pathname==='/api/fs/git-history')await new Promise(release=>p.pending.push({kind,release}));
          return original;
        });
      };
    """)
    ActionChains(browser).context_click(finder_row).perform()
    # Deferred metadata replaces the pending menu; use its completed public affordance.
    wait.until(lambda driver: driver.execute_script('''
      return [...document.querySelectorAll('.file-context-menu button')].some(node=>
        node.getClientRects().length>0&&!node.disabled&&node.textContent.trim()==='Copy relative path');
    '''))
    action = wait.until(lambda driver: next((node for node in driver.find_elements(
        By.CSS_SELECTOR, '.file-context-menu button'
    ) if node.is_displayed() and node.text.strip() == 'ΔShow Diff'), None))
    action.click()
    panel = wait.until(lambda driver: next((node for node in driver.find_elements(
        By.CSS_SELECTOR, '.git-diff-panel'
    ) if node.is_displayed()), None))
    try:
        commit = wait.until(lambda _: panel.find_element(By.CSS_SELECTOR, f'[data-git-diff-commit-group="{head}"] .git-diff-commit-row'))
    except Exception:
        print('Native Repo Diff load diagnostic:', browser.execute_script('''
          const p=__nativeRepoReading;return {url:location.href,panel:arguments[0].textContent,
            responses:p.responses.map(({kind,status,data})=>({kind,status,data})),
            errors:typeof jsDebugFailureEvents==='function'?jsDebugFailureEvents():null};
        ''', panel))
        raise
    commit.click()
    message_node = wait.until(lambda _: panel.find_element(By.CSS_SELECTOR, '.git-diff-commit-message'))
    assert message_node.text.rstrip() == message
    wait.until(lambda _: panel.find_elements(By.CSS_SELECTOR, '.git-diff-worktree-summary .git-diff-commit-added'))
    browser.execute_script("""
      const p=window.__nativeRepoReading;p.panel=arguments[0];p.message=arguments[1];
      p.body=p.panel.querySelector('.git-diff-panel-body');p.expectedText=p.message.textContent;
    """, panel, message_node)
    ActionChains(browser).scroll_from_origin(ScrollOrigin.from_element(message_node), 0, 180).perform()
    wait.until(lambda driver: driver.execute_script('return __nativeRepoReading.message.scrollTop>40'))
    directory = panel.find_element(By.CSS_SELECTOR, '.git-diff-commit-detail .file-tree-row[data-kind="dir"]')
    browser.execute_script("""
      const p=__nativeRepoReading;p.beforeDisclosure={scroll:p.message.scrollTop,body:p.body.scrollTop};
    """)
    directory.click()
    browser.execute_script("""
      const p=__nativeRepoReading;p.focus=arguments[0];const rect=p.message.getBoundingClientRect();
      p.expected={scroll:p.message.scrollTop,body:p.body.scrollTop,top:rect.top,height:rect.height};
      p.expanded=p.focus.getAttribute('aria-expanded');
    """, directory)

    def snapshot():
        return browser.execute_script("""
          const p=__nativeRepoReading,r=p.message.getBoundingClientRect();
          return {sameMessage:p.panel.querySelector('.git-diff-commit-message')===p.message,
            scroll:p.message.scrollTop,body:p.body.scrollTop,expected:p.expected,
            focused:document.activeElement===p.focus,connected:p.focus.isConnected,
            expanded:p.focus.getAttribute('aria-expanded'),expectedExpanded:p.expanded,
            top:r.top,height:r.height,text:p.message.textContent,expectedText:p.expectedText,
            busy:p.panel.querySelector('.git-diff-refresh').getAttribute('aria-busy'),
            refreshDisabled:p.panel.querySelector('.git-diff-refresh').disabled};
        """)

    def assert_reading():
        for settled in (False, True):
            if settled:
                browser.execute_async_script('const done=arguments[arguments.length-1];requestAnimationFrame(()=>requestAnimationFrame(done));')
            state = snapshot()
            assert state['sameMessage'] and state['focused'] and state['connected'], state
            assert abs(state['scroll'] - state['expected']['scroll']) <= 1, state
            assert abs(state['body'] - state['expected']['body']) <= 1, state
            assert abs(state['top'] - state['expected']['top']) <= 1, state
            assert abs(state['height'] - state['expected']['height']) <= 1, state
            assert state['expanded'] == state['expectedExpanded'], state
            assert state['text'] == state['expectedText'], state
    assert_reading()
    disclosure = browser.execute_script('return __nativeRepoReading.beforeDisclosure')
    assert abs(snapshot()['scroll'] - disclosure['scroll']) <= 1, snapshot()
    assert abs(snapshot()['body'] - disclosure['body']) <= 1, snapshot()
    screenshot = Path(os.environ.get('YOMUX_E2E_EVIDENCE_DIR', str(tmp_path))) / f'repo-diff-native-reading-{refresh}.png'
    assert browser.save_screenshot(str(screenshot))
    initial = browser.execute_script("""
      const p=__nativeRepoReading;
      return p.responses.filter(row=>row.kind==='only').at(-1)?.data.worktree_status;
    """)
    assert initial and initial['files'] == 1, initial
    dirty.write_text(''.join(f'updated dirty line {index}\n' for index in range(40)), encoding='utf-8')
    browser.execute_script('const p=__nativeRepoReading;p.hold=true;p.responses=[];')
    if refresh == 'manual':
        panel.find_element(By.CSS_SELECTOR, '.git-diff-refresh').click()
    wait.until(lambda driver: driver.execute_script("return __nativeRepoReading.pending.some(row=>row.kind==='0')"))
    held = snapshot()
    print('Native held refresh geometry:', json.dumps({key: value for key, value in held.items() if key not in {'text', 'expectedText'}}))
    assert held['busy'] == 'true' and held['refreshDisabled'], held
    assert held['sameMessage'] and abs(held['scroll'] - held['expected']['scroll']) <= 1, held
    assert abs(held['body'] - held['expected']['body']) <= 1, held
    assert abs(held['top'] - held['expected']['top']) <= 1, held
    assert abs(held['height'] - held['expected']['height']) <= 1, held
    if refresh == 'automatic':
        assert held['focused'] and held['connected'], held
    directory.click()
    browser.execute_script("""
      const p=__nativeRepoReading;p.focus=arguments[0];
      p.expanded=p.focus.getAttribute('aria-expanded');
    """, directory)
    assert_reading()
    browser.execute_script("const p=__nativeRepoReading;p.pending.splice(p.pending.findIndex(row=>row.kind==='0'),1)[0].release();")
    wait.until(lambda driver: driver.execute_script("return __nativeRepoReading.pending.some(row=>row.kind==='only')"))
    assert_reading()
    changed = browser.execute_script("""
      const p=__nativeRepoReading,row=p.responses.find(row=>row.kind==='only');
      p.hold=false;p.pending.splice(p.pending.findIndex(row=>row.kind==='only'),1)[0].release();return row;
    """)
    assert changed['status'] == 200 and changed['data']['worktree_status']['files'] == 1, changed
    added = changed['data']['worktree_status']['added']
    assert added > initial['added'], (initial, changed)
    wait.until(lambda _: panel.find_element(By.CSS_SELECTOR, '.git-diff-worktree-summary .git-diff-commit-added').text == f'+{added}')
    assert_reading()
    responses = browser.execute_script('return __nativeRepoReading.responses.map(({kind,status,data})=>({kind,status,head:data.head,repo:data.repo}))')
    assert {row['kind'] for row in responses} >= {'0', 'only'}, responses
    assert all(row['status'] == 200 for row in responses), responses
    assert next(row for row in responses if row['kind'] == '0')['head'] == head, responses
    finished = snapshot()
    assert finished['busy'] == 'false' and not finished['refreshDisabled'], finished
    diagnostics = browser.execute_script('return jsDebugFailureEvents()')
    assert not diagnostics, diagnostics
    reading = {key: value for key, value in snapshot().items() if key not in {'text', 'expectedText'}}
    print('Native Repo Diff reading:', json.dumps({'head': head, 'before': initial, 'after': changed['data']['worktree_status'],
                                                  'reading': reading, 'responses': responses, 'screenshot': str(screenshot)}))
