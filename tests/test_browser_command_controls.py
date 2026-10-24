from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from tests.browser_helpers.browser_console import consume_only_expected_js_debug_api_errors


def test_visible_event_frame_keeps_disjoint_patches_and_scoped_signals(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2", sessions=["1", "2"])
    metrics = browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof handleClientPushEvent === 'function' && document.querySelector('[data-pane-tab="2"]'), {timeoutMs:5000});
        const originalFrame = window.requestAnimationFrame, originalCancel = window.cancelAnimationFrame;
        const frames = new Map();
        let sequence = 1000000;
        window.requestAnimationFrame = callback => { const id = ++sequence; frames.set(id, callback); return id; };
        window.cancelAnimationFrame = id => frames.delete(id) || originalCancel(id);
        try {
          const envelope = (resource, revision, base) => ({epoch:'browser-event-queue', resource, resource_revision:revision,
            ...(base ? {base_resource_revision:base} : {})});
          handleClientPushEvent('auto_approve_changed', {data:{topology_generation:tmuxTopologyGeneration,
            agent_window_snapshot_revision:400, session_order:['1','2'],
            sessions:{'1':{target:'1',enabled:false},'2':{target:'2',enabled:false}}, rules:{mode:'safe'}}},
            envelope('auto_approve_changed',400));
          for (const [index, session] of ['1','2'].entries()) {
            handleClientPushEvent('auto_approve_changed', {patch:true, collection:'sessions',
              changes:{[session]:{target:session,enabled:true}}, removed_keys:[],
              fields:{agent_window_snapshot_revision:401+index}, removed_fields:[]},
              envelope('auto_approve_changed',401+index,400+index));
            handleClientPushEvent('search_progress', {scope_id:'scope-'+session,generation:1,index_state:'error'},
              envelope('search_progress:scope-'+session,1));
            handleClientPushEvent('attention_acks_changed', {acknowledged:['ack-'+session]},
              envelope('attention_acks_changed',index+1));
          }
          const queued = clientEventTransportState.queue.size;
          const before = ['1','2'].map(session => autoApproveStates.get(session)?.enabled === true);
          window.requestAnimationFrame = originalFrame;
          const held = [...frames.values()]; frames.clear();
          for (const frame of held) frame(performance.now());
          const immediate = ['1','2'].map(session => ({enabled:autoApproveStates.get(session)?.enabled === true,
            marker:Boolean(document.querySelector('[data-yolo-session="'+session+'"].active'))}));
          await new Promise(originalFrame);
          return {visible:document.visibilityState, queued, before, immediate,
            settled:['1','2'].map(session => autoApproveStates.get(session)?.enabled === true),
            scopes:[...fileQuickOpenState.searchProgressSequences.keys()].filter(key => key.startsWith('scope-')).sort(),
            acknowledgements:['1','2'].map(session => attentionAcknowledgementKeyIsRecorded('ack-'+session)),
            revision:clientEventTransportState.resourceRevisions.get('auto_approve_changed')};
        } finally { window.requestAnimationFrame = originalFrame; window.cancelAnimationFrame = originalCancel; }
      })().then(done, error => done({error:String(error.stack || error)}));
    """)
    assert "error" not in metrics, metrics
    assert metrics["visible"] == "visible" and metrics["queued"] == 7, metrics
    assert metrics["before"] == [False, False], metrics
    assert metrics["immediate"] == [{"enabled": True, "marker": True}, {"enabled": True, "marker": True}], metrics
    assert metrics["settled"] == metrics["acknowledgements"] == [True, True], metrics
    assert metrics["scopes"] == ["scope-1", "scope-2"] and metrics["revision"] == 402, metrics


def test_native_stream_readers_retire_deadlines_and_keep_upstream_abort_identity(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    metrics = browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof apiFetchResponseWithDeadline === 'function', {timeoutMs:5000});
        const results = [];
        for (const mode of ['eof', 'cancel', 'upstream-abort', 'deadline']) {
          let cleanup = 0, consumed = 0, timeouts = 0;
          const failure = new DOMException('aborted', 'AbortError');
          const deadline = Object.assign(new Error('deadline expired'), {code:'deadline_expired'});
          const response = apiFetchResponseWithDeadline(new Response(new ReadableStream({start(controller) {
            if (mode === 'eof') controller.close();
            else if (mode !== 'cancel') controller.error(failure);
          }})), {cleanup:()=>cleanup++, onConsumed:()=>consumed++, noteTimeout:()=>timeouts++,
            timeoutError:()=>mode === 'deadline' ? deadline : null});
          const reader = response.body.getReader();
          let identity = true;
          try {
            if (mode === 'cancel') await reader.cancel();
            else await reader.read();
            if (mode === 'deadline' || mode === 'upstream-abort') identity = false;
          } catch (error) { identity = error === (mode === 'deadline' ? deadline : failure); }
          results.push({mode, cleanup, consumed, timeouts, identity});
        }
        return results;
      })().then(done, error => done({error:String(error.stack || error)}));
    """)
    assert isinstance(metrics, list), metrics
    for row in metrics:
        assert row["cleanup"] == row["consumed"] == 1 and row["identity"] is True, row
        assert row["timeouts"] == (1 if row["mode"] == "deadline" else 0), row


def test_summary_terminal_frames_paint_before_stream_retirement(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    metrics = browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof activateTab === 'function' && document.getElementById('summary-1'), {timeoutMs:5000});
        const originalSource = window.EventSource;
        const originalFrame = window.requestAnimationFrame;
        const sources = [], frames = [], results = [];
        window.EventSource = class extends EventTarget {
          constructor(url) {
            super();
            if (!String(url).startsWith('/api/summary-stream?')) return new originalSource(url);
            this.closeCount = 0; sources.push(this);
          }
          close() { this.closeCount++; }
        };
        window.requestAnimationFrame = callback => callback.name === 'renderSummary'
          ? frames.push(callback) : originalFrame(callback);
        try {
          for (const ending of ['done', 'summary_error', 'disconnect', 'replace']) {
            activateTab('1', 'summary');
            const source = sources.at(-1);
            source.dispatchEvent(new MessageEvent('delta', {data:JSON.stringify({text:'Final summary sentence.'})}));
            if (ending === 'replace') startSummaryStream('1');
            else if (ending === 'disconnect') source.onerror();
            else source.dispatchEvent(new MessageEvent(ending, {data:JSON.stringify({error:'provider failed', return_code:7})}));
            const immediate = document.getElementById('summary-1').textContent;
            for (const frame of frames.splice(0)) frame();
            await new Promise(originalFrame);
            const settled = document.getElementById('summary-1').textContent;
            results.push({ending, immediate, settled, closed:source.closeCount});
          }
          return results;
        } finally {
          activateTab('1', 'terminal');
          window.EventSource = originalSource;
          window.requestAnimationFrame = originalFrame;
        }
      })().then(done, error => done({error:String(error.stack || error)}));
    """)
    assert isinstance(metrics, list), metrics
    for row in metrics:
        expected = row["ending"] != "replace"
        assert ("Final summary sentence." in row["immediate"]) is expected, row
        assert ("Final summary sentence." in row["settled"]) is expected, row
        assert row["closed"] == 1, row


def test_api_stream_debug_observation_does_not_keep_a_cancelled_producer_alive(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    metrics = browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof apiFetch === 'function', {timeoutMs:5000});
        const originalFetch = window.fetch, originalTimer = window.setTimeout, originalClear = window.clearTimeout;
        const upstream = new AbortController();
        const originalAdd = upstream.signal.addEventListener.bind(upstream.signal);
        const originalRemove = upstream.signal.removeEventListener.bind(upstream.signal);
        let listeners = 0, ownedTimer, timerCleared = false, producerCancels = 0, controller;
        upstream.signal.addEventListener = (...args) => { listeners++; return originalAdd(...args); };
        upstream.signal.removeEventListener = (...args) => { listeners--; return originalRemove(...args); };
        const chunk = new TextEncoder().encode('event: done\\ndata: {}\\n\\n');
        window.setTimeout = (callback, delay, ...args) => {
          const id = originalTimer(callback, delay, ...args);
          if (delay === 60617) ownedTimer = id;
          return id;
        };
        window.clearTimeout = id => { if (id === ownedTimer) timerCleared = true; return originalClear(id); };
        window.fetch = (url, options) => String(url) === '/api/review-stream-cancellation'
          ? Promise.resolve(new Response(new ReadableStream({start(value) {controller = value; value.enqueue(chunk); value.enqueue(chunk);},
            cancel() { producerCancels++; }}), {headers:{'Content-Type':'text/event-stream'}}))
          : originalFetch(url, options);
        let cancellation;
        try {
          const response = await apiFetch('/api/review-stream-cancellation', {startupImmediate:true, deadlineMs:60617, signal:upstream.signal});
          const reader = response.body.getReader();
          const event = [...jsDebugEvents].reverse().find(value => value.endpoint === '/api/review-stream-cancellation');
          const observation = jsDebugCurrentObservationState.queue.find(value => value.liveEvent === event);
          await reader.read();
          const finalizedAfterFirstChunk = observation?.finalized;
          await reader.read();
          let settled = false;
          cancellation = reader.cancel().then(() => { settled = true; });
          await new Promise(resolve => {
            const channel = new MessageChannel();
            channel.port1.onmessage = () => { channel.port1.close(); channel.port2.close(); resolve(); };
            channel.port2.postMessage(null);
          });
          return {settled, producerCancels, timerCleared, listeners, responseBytes:event?.responseBytes, chunkBytes:2*chunk.byteLength,
            finalizedAfterFirstChunk, finalized:observation?.finalized, persistedBytes:observation?.event.responseBytes};
        } finally {
          if (!producerCancels && controller) controller.close();
          if (cancellation) await cancellation;
          window.fetch = originalFetch; window.setTimeout = originalTimer; window.clearTimeout = originalClear;
        }
      })().then(done, error => done({error:String(error.stack || error)}));
    """)
    assert "error" not in metrics, metrics
    assert metrics["settled"] is True and metrics["producerCancels"] == 1, metrics
    assert metrics["timerCleared"] is True and metrics["listeners"] == 0, metrics
    assert metrics["responseBytes"] == metrics["chunkBytes"], metrics
    assert metrics["finalizedAfterFirstChunk"] is False and metrics["finalized"] is True, metrics
    assert metrics["persistedBytes"] == metrics["chunkBytes"], metrics


def test_shared_button_overlay_and_command_lifecycle(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    metrics = browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof dispatchCommand === 'function' && typeof createToolbarButton === 'function',
          {timeoutMs: 5000, description: 'shared command and button owners'});
        const host = document.createElement('div');
        document.body.append(host);
        const direct = makeButton({label: 'Direct', className: 'command-control-probe', ariaLabel: 'Direct'});
        const toolbar = createToolbarButton({label: 'Toolbar', className: 'command-control-probe', ariaLabel: 'Toolbar', pressed: false});
        host.append(direct, toolbar);
        const overlay = document.createElement('div');
        const controller = createDismissableOverlayController();
        host.append(overlay);
        const originalFetch = window.fetch;
        const requests = [];
        try {
          const button = {directClass: direct.className, toolbarClass: toolbar.className,
            directFont: getComputedStyle(direct).font, toolbarFont: getComputedStyle(toolbar).font,
            pressed: toolbar.getAttribute('aria-pressed')};
          let propagated = 0;
          let focusReturned = true;
          for (let index = 0; index < 3; index += 1) {
            controller.open(overlay, {trigger: toolbar});
            const event = new Event('pointerdown', {bubbles: true});
            event.stopPropagation = () => { propagated += 1; };
            overlay.dispatchEvent(event);
            controller.close();
            focusReturned = focusReturned && document.activeElement === toolbar;
          }
          window.fetch = (url, options) => String(url).startsWith('/api/settings')
            ? new Promise(resolve => requests.push({url, options, resolve})) : originalFetch(url, options);
          statusOk('codex ready');
          const priorFooter = statusEl.innerHTML;
          const first = dispatchCommand(COMMAND_ROUTES['settings-save'], {payload: {theme: 'dark'}}, toolbar);
          const duplicate = dispatchCommand(COMMAND_ROUTES['settings-save'], {payload: {theme: 'dark'}}, toolbar);
          const second = dispatchCommand(COMMAND_ROUTES['settings-save'], {payload: {font_size: 18}}, toolbar);
          await window.__yomuxTestWaitFor(() => requests.length === 2,
            {timeoutMs: 5000, description: 'independent settings patches'});
          const pendingStatuses = toolbar.getAttribute('aria-describedby').split(' ').map(id => document.getElementById(id));
          const pending = toolbar.disabled && toolbar.getAttribute('aria-busy') === 'true'
            && pendingStatuses.length === 2 && pendingStatuses.every(node => node?.dataset.commandStatusPhase === 'pending');
          requests[0].resolve(new Response(JSON.stringify({saved: true}), {status: 200}));
          await first;
          const stillPending = toolbar.disabled;
          requests[1].resolve(new Response(JSON.stringify({saved: true}), {status: 200}));
          await Promise.all([duplicate, second]);
          done({button, propagated, sameDuplicate: first === duplicate, requestCount: requests.length,
            pending, stillPending, settled: !toolbar.disabled && toolbar.getAttribute('aria-busy') === null,
            residualStatuses: pendingStatuses.filter(node => node.isConnected).length,
            focusReturned, footerRestored: statusEl.innerHTML === priorFooter});
        } finally {
          controller.close();
          window.fetch = originalFetch;
          host.remove();
        }
      })().catch(error => done({error: String(error.stack)}));
    """)
    assert "error" not in metrics, metrics
    assert metrics["button"]["directClass"] == metrics["button"]["toolbarClass"] == "btn-base command-control-probe", metrics
    assert metrics["button"]["directFont"] == metrics["button"]["toolbarFont"], metrics
    assert metrics["button"]["pressed"] == "false", metrics
    assert metrics["propagated"] == 3, metrics
    assert metrics["sameDuplicate"] is True and metrics["requestCount"] == 2, metrics
    assert metrics["pending"] is True and metrics["stillPending"] is True, metrics
    assert metrics["settled"] is True and metrics["residualStatuses"] == 0, metrics
    assert metrics["focusReturned"] is True, metrics
    assert metrics["footerRestored"] is True, metrics


@pytest.mark.parametrize("failure, expected", [
    ({"error": "file changed on disk", "error_code": "changed_on_disk", "message_key": "fs.error.changedOnDisk", "path": "/tmp/example.md"}, "File changed on disk: /tmp/example.md."),
    ({"error": "deadline_expired: request exceeded its 15s deadline", "reason_code": "deadline_expired"}, "deadline_expired: request exceeded its 15s deadline"),
    ({"localDeadline": True}, "deadline_expired: request exceeded its 15s deadline"),
])
def test_command_notice_uses_timed_yellow_toast(browser, tmp_path, failure, expected):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    metrics = browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      const failure = arguments[0];
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof dispatchCommand === 'function' && typeof showToast === 'function',
          {timeoutMs: 5000, description: 'command and toast owners'});
        const menu = document.querySelector('.app-menu[data-app-menu="view"] > .app-menu-button');
        menu.click();
        menu.focus();
        const priorFocus = document.activeElement;
        const originalFetch = window.fetch;
        const originalDuration = toastDurationMs;
        toastDurationMs = 400;
        window.fetch = (url, options) => String(url) === '/api/fs/write'
          ? (failure.localDeadline ? Promise.reject(apiFetchDeadlineError(15000))
            : Promise.resolve(new Response(JSON.stringify(failure), {status:409})))
          : originalFetch(url, options);
        try {
          await dispatchCommand(COMMAND_ROUTES['finder-file-create'], {payload:{path:'/tmp/example.md'},
            internalOptions:{quietStatuses:[409]}}).catch(() => {});
          const toast = document.querySelector('.attention-alerts .toast');
          const rect = document.querySelector('.app-menu[data-app-menu="view"] > .app-menu-popover').getBoundingClientRect();
          const point = {x:rect.left + rect.width / 2, y:rect.top + Math.min(32, rect.height / 2)};
          const toastRect = toast?.getBoundingClientRect();
          const result = {message: toast?.textContent.replace(/\\s+/g, ' ').trim() || '',
            persistent: toast?.dataset.toastPersistent === 'true', remaining: Number(toast?.dataset.toastRemainingMs || 0),
            background: toast ? getComputedStyle(toast).backgroundColor : '',
            toastBelowMenu: Boolean(toastRect && toastRect.top >= rect.bottom),
            menuOpen: document.querySelector('.app-menu[data-app-menu="view"]').classList.contains('open'),
            menuReceivesHit: document.elementFromPoint(point.x, point.y)?.closest('.app-menu-popover') === document.querySelector('.app-menu[data-app-menu="view"] > .app-menu-popover'),
            globalDanger: document.getElementById('status').classList.contains('layout-status-danger'),
            focusPreserved: document.activeElement === priorFocus,
            commandErrors: document.querySelectorAll('.command-error-status').length};
          await window.__yomuxTestWaitFor(() => !toast?.isConnected,
            {timeoutMs: 5000, description: 'command notice expires without dismissal'});
          result.expired = !toast?.isConnected;
          return result;
        } finally {
          window.fetch = originalFetch;
          toastDurationMs = originalDuration;
          closeAppMenus();
        }
      })().then(done, error => done({error:String(error.stack || error)}));
    """, failure)
    assert "error" not in metrics, metrics
    assert expected in metrics["message"], metrics
    assert metrics["persistent"] is False and metrics["remaining"] > 0, metrics
    assert "255, 244, 194" in metrics["background"], metrics
    assert metrics["toastBelowMenu"] is True, metrics
    assert metrics["menuOpen"] is True and metrics["menuReceivesHit"] is True, metrics
    assert metrics["globalDanger"] is False and metrics["commandErrors"] == 0, metrics
    assert metrics["expired"] is True, metrics
    assert metrics["focusPreserved"] is True, metrics
    if failure.get("localDeadline"):
        consume_only_expected_js_debug_api_errors(browser, [{"method": "POST", "path": "/api/fs/write",
            "status": None, "ok": None, "error": "deadline_expired: request exceeded its 15s deadline"}])


def test_changed_on_disk_toasts_stack_without_overlap(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    metrics = browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof showToast === 'function',
          {timeoutMs: 5000, description: 'toast stack owner'});
        const container = document.querySelector('.attention-alerts');
        const first = showToast('', 'First conflict', {className:'attention-alert toast command-notice'});
        const second = showToast('', 'Second conflict', {className:'attention-alert toast command-notice'});
        const firstRect = first.getBoundingClientRect();
        const secondRect = second.getBoundingClientRect();
        const stackRect = container.getBoundingClientRect();
        const result = {
          first: {top:firstRect.top, bottom:firstRect.bottom, left:firstRect.left, right:firstRect.right},
          second: {top:secondRect.top, bottom:secondRect.bottom, left:secondRect.left, right:secondRect.right},
          stack: {top:stackRect.top, bottom:stackRect.bottom},
          gap: secondRect.top - firstRect.bottom,
          viewportBottom: innerHeight,
        };
        first.querySelector('[data-toast-close]')?.click();
        second.querySelector('[data-toast-close]')?.click();
        done(result);
      })().catch(error => done({error:String(error.stack || error)}));
    """)
    assert "error" not in metrics, metrics
    assert metrics["gap"] >= 0, metrics
    assert metrics["second"]["top"] > metrics["first"]["top"], metrics
    assert metrics["first"]["right"] == metrics["second"]["right"], metrics
    assert metrics["stack"]["bottom"] <= metrics["viewportBottom"], metrics


def test_index_action_uses_timed_notice_without_overwriting_pending_command(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    metrics = browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof setFileExplorerDirectoryIndexed === 'function',
          {timeoutMs: 5000, description: 'index action owner'});
        const originalFetch = window.fetch;
        let release = null;
        const originalDuration = toastDurationMs;
        toastDurationMs = 300;
        window.fetch = (url, options) => {
          if (String(url) === '/api/settings' && !release) return new Promise(resolve => { release = resolve; });
          if (String(url).startsWith('/api/fs/index-status')) return Promise.resolve(new Response(JSON.stringify({status:'ready'}), {status:200}));
          return originalFetch(url, options);
        };
        try {
          const pending = dispatchCommand(COMMAND_ROUTES['settings-save'], {payload:{theme:'dark'}});
          await window.__yomuxTestWaitFor(() => release !== null,
            {timeoutMs: 5000, description: 'held settings command'});
          const pendingNode = statusEl.firstChild;
          const sharedNotice = showTimedStatusNotice('shared owner probe');
          const parentPreservesNode = statusEl.firstChild === pendingNode;
          sharedNotice.querySelector('[data-toast-close]').click();
          setFileExplorerDirectoryIndexed('/home/test/notes', true);
          const notice = [...document.querySelectorAll('.attention-alerts .toast')].find(node => node.textContent.includes('Indexed'));
          const result = {notice: notice?.textContent || '', pendingPreserved: commandOwnsGlobalStatus(), parentPreservesNode,
            status: statusEl.textContent, fixedIndexed: statusEl.classList.contains('layout-status-visible') && statusEl.textContent.includes('Indexed'),
            timed: Number(notice?.dataset.toastRemainingMs || 0) > 0};
          release(new Response(JSON.stringify({saved:true}), {status:200}));
          await pending;
          await window.__yomuxTestWaitFor(() => !notice.isConnected,
            {timeoutMs: 5000, description: 'index notice expiry'});
          result.expired = !notice.isConnected;
          return result;
        } finally {
          window.fetch = originalFetch;
          toastDurationMs = originalDuration;
        }
      })().then(done, error => done({error:String(error.stack || error)}));
    """)
    assert "error" not in metrics, metrics
    assert "Indexed" in metrics["notice"] and metrics["timed"] is True, metrics
    assert metrics["pendingPreserved"] is True and metrics["fixedIndexed"] is False, metrics
    assert metrics["parentPreservesNode"] is True and metrics["expired"] is True, metrics


def test_legacy_footer_updates_and_late_command_settlement_preserve_authentication(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    metrics = browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof dispatchCommand === 'function', {timeoutMs:5000});
        const originalFetch = window.fetch;
        let release;
        window.fetch = (url, options) => String(url) === '/api/settings'
          ? new Promise(resolve => { release = resolve; }) : originalFetch(url, options);
        try {
          const pending = dispatchCommand(COMMAND_ROUTES['settings-save'], {payload:{theme:'dark'}});
          await window.__yomuxTestWaitFor(() => Boolean(release), {timeoutMs:5000});
          showLayoutStatus('plain completion');
          const plainClean = !statusEl.classList.contains('layout-status-visible') && statusEl.getAttribute('aria-live') === 'polite';
          setStatusHtml('<a href="/example">saved &amp; linked</a>');
          const html = statusEl.innerHTML;
          release(new Response(JSON.stringify({saved:true}), {status:200}));
          await pending;
          const lateSettlePreserved = statusEl.innerHTML === html;
          release = null;
          const second = dispatchCommand(COMMAND_ROUTES['settings-save'], {payload:{theme:'light'}});
          await window.__yomuxTestWaitFor(() => Boolean(release), {timeoutMs:5000});
          claimTerminalAuthentication(new Response('{}', {status:401}));
          const auth = statusEl.outerHTML;
          showLayoutStatus('late text callback');
          setStatusHtml('<a href="/late">late HTML callback</a>');
          showTimedStatusNotice('late notice callback');
          release(new Response(JSON.stringify({saved:true}), {status:200}));
          await second;
          const signedOut = dispatchCommand(COMMAND_ROUTES['settings-save'], {payload:{theme:'dark'}});
          const pendingPreserved = statusEl.outerHTML === auth;
          const signedOutState = [...commandGlobalPending].at(-1);
          publishCommandOverdueStatus(signedOutState);
          const overduePreserved = statusEl.outerHTML === auth;
          let rejectedForAuthentication = false;
          try { await signedOut; } catch (error) { rejectedForAuthentication = error.terminalAuthentication === true; }
          return {plainClean, html, lateSettlePreserved, authPreserved: statusEl.outerHTML === auth,
            pendingPreserved, overduePreserved, rejectedForAuthentication,
            role: statusEl.getAttribute('role'), live: statusEl.getAttribute('aria-live')};
        } finally { window.fetch = originalFetch; }
      })().then(done, error => done({error:String(error.stack || error)}));
    """)
    assert "error" not in metrics, metrics
    assert metrics["plainClean"] is True and metrics["lateSettlePreserved"] is True, metrics
    assert metrics["html"] == '<a href="/example">saved &amp; linked</a>', metrics
    assert metrics["authPreserved"] is True and metrics["role"] == "alert" and metrics["live"] == "assertive", metrics
    assert metrics["pendingPreserved"] is True and metrics["overduePreserved"] is True and metrics["rejectedForAuthentication"] is True, metrics


def test_api_debug_phases_do_not_reuse_a_full_native_resource_buffer(browser, tmp_path):
    preload = browser.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "window.__nativeResourceFetch = window.fetch.bind(window);",
    })
    try:
        load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    finally:
        browser.execute_cdp_cmd("Page.removeScriptToEvaluateOnNewDocument", {"identifier": preload["identifier"]})
    metrics = browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof apiFetch === 'function' && typeof jsDebugResourceTimingEntry === 'function',
          {timeoutMs: 5000, description: 'API resource timing owners'});
        const url = '/static/tmux-wall.css?resource-timing=' + crypto.randomUUID();
        const absolute = new URL(url, location.href).href;
        const observed = [];
        const observer = new PerformanceObserver(list => observed.push(...list.getEntries().filter(entry => entry.name === absolute)));
        observer.observe({type: 'resource'});
        const originalFetch = window.fetch;
        try {
          performance.clearResourceTimings();
          performance.setResourceTimingBufferSize(1);
          await (await window.__nativeResourceFetch(url, {cache: 'no-store'})).arrayBuffer();
          await window.__yomuxTestWaitFor(() => performance.getEntriesByName(absolute).length === 1,
            {timeoutMs: 5000, description: 'retained native fetch resource entry'});
          const retained = performance.getEntriesByName(absolute)[0];
          const requestStart = performance.now();
          window.fetch = (input, options) => String(input).startsWith(url)
            ? window.__nativeResourceFetch(input, options) : originalFetch(input, options);
          await (await apiFetch(url, {startupImmediate: true, cache: 'no-store'})).arrayBuffer();
          await window.__yomuxTestWaitFor(() => observed.length >= 2,
            {timeoutMs: 5000, description: 'second native fetch completes despite exhausted resource buffer'});
          const event = [...jsDebugEvents].reverse().find(entry => entry.type === 'api' && entry.endpoint === '/static/tmux-wall.css');
          const networkFields = ['queueMs', 'connectMs', 'tlsMs', 'ttfbMs', 'downloadMs'];
          const bufferedStarts = performance.getEntriesByName(absolute).map(entry => entry.startTime);
          performance.setResourceTimingBufferSize(250);
          const freshUrl = url + '&fresh=1';
          const freshAbsolute = new URL(freshUrl, location.href).href;
          const freshWindow = {};
          await (await fetchApiRequestWithTiming(freshUrl, {cache: 'no-store'}, freshWindow)).arrayBuffer();
          await window.__yomuxTestWaitFor(() => performance.getEntriesByName(freshAbsolute).length === 1,
            {timeoutMs: 5000, description: 'current native fetch entry becomes available'});
          const freshEntry = jsDebugResourceTimingEntry(freshUrl, freshWindow);
          return {native: String(window.__nativeResourceFetch).includes('[native code]'),
            retainedStart: retained.startTime, requestStart,
            bufferedStarts, freshMatched: Boolean(freshEntry && freshEntry.startTime >= freshWindow.start && freshEntry.startTime <= freshWindow.end),
            completedFetches: observed.length, status: event?.status,
            borrowedPhases: networkFields.filter(key => Object.hasOwn(event?.phaseTimings || {}, key)),
            protocol: event?.connectionProtocol || ''};
        } finally {
          observer.disconnect();
          performance.setResourceTimingBufferSize(250);
          window.fetch = originalFetch;
        }
      })().then(done, error => done({error:String(error.stack || error)}));
    """)
    assert "error" not in metrics, metrics
    assert metrics["native"] is True and metrics["completedFetches"] >= 2, metrics
    assert metrics["status"] == 200 and metrics["retainedStart"] < metrics["requestStart"], metrics
    assert metrics["bufferedStarts"] == [metrics["retainedStart"]], metrics
    assert metrics["freshMatched"] is True, metrics
    assert metrics["borrowedPhases"] == [] and metrics["protocol"] == "", metrics
