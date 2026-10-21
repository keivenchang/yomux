from tests.browser_helpers.browser_layout import *  # noqa: F401,F403


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
