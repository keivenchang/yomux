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

from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401
from tests.tmux_runtime import run_isolated_tmux


pytestmark = pytest.mark.browser


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
    cpu_before = Path(f'/proc/{server.process.pid}/stat').read_text().split(') ', 1)[1].split()
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
          return {summaryText,snapshots,removed,treeWrites,summaryWrites,converged:state.payload.preparation.partial===false};
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
    assert all(sample['text'] == progress_render['summaryText'] and sample['sameSummary']
               and sample['sameRows'] and (sample['phase'].startswith('1:') or not sample['loading'])
               for sample in progress_render['snapshots']), progress_render
    cpu_after = Path(f'/proc/{server.process.pid}/stat').read_text().split(') ', 1)[1].split()
    resource = {'cpu_seconds': (sum(int(cpu_after[i]) for i in (11, 12)) - sum(int(cpu_before[i]) for i in (11, 12))) / os.sysconf('SC_CLK_TCK'),
                'rss_bytes': int(cpu_after[21]) * os.sysconf('SC_PAGE_SIZE'), 'observed_seconds': time.monotonic() - measurement_started}
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
