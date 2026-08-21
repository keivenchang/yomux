"""Shared editor gate fixture and browser scenario builders."""

import json
import os
import time
from http import HTTPStatus
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import assert_live_runtime_boot_healthy
from tests.browser_helpers.browser_layout import start_browser_server
from tests.browser_helpers.browser_layout import start_isolated_browser_app
from tests.browser_helpers.browser_layout import stop_browser_server
from tests.browser_helpers.browser_layout import stop_isolated_browser_app
from tests.gate_harness import wait_for_browser_boot

EDITOR_GLOBALS = {
    "fileEditorItemFor": "function",
    "fileEditorPanelsForPath": "function",
    "markOpenFileMissing": "function",
    "openFileInEditor": "function",
    "clientEventDemandDescriptor": "function",
}

@pytest.fixture
def gate_browser_runtime(browser, monkeypatch, gate_runtime_paths):
    runtime = start_isolated_browser_app(monkeypatch, gate_runtime_paths.root, dangerously_yolo=False)
    assert runtime.paths.config_dir.parent == gate_runtime_paths.root
    assert runtime.paths.state_dir.parent == gate_runtime_paths.root
    auto_approve_payload = {
        "session_order": runtime.sessions,
        "sessions": {
            session: {"target": session, "enabled": False, "last_action": "off"}
            for session in runtime.sessions
        },
        "rules": {"path": str(gate_runtime_paths.config_dir / "yolo-rules.yaml"), "source": "default", "rules": [], "errors": []},
    }
    monkeypatch.setattr(
        runtime.app,
        "auto_approve_status_bytes",
        lambda session=None: (json.dumps(auto_approve_payload).encode("utf-8"), HTTPStatus.OK),
    )
    server, thread = start_browser_server(monkeypatch, gate_runtime_paths.config_dir, runtime.app, auth_bypass=True)
    session = runtime.sessions[0]
    browser.get(f"http://127.0.0.1:{server.server_address[1]}/?{urlencode({'sessions': session, 'layout': 'left', 'tabs': f'left:{session}'})}")
    assert_live_runtime_boot_healthy(browser, "regression-gate", timeout=12)
    wait_for_browser_boot(browser, globals_required=EDITOR_GLOBALS, dom_anchors=("#grid",), timeout=12)
    WebDriverWait(browser, 12).until(
        lambda driver: driver.execute_script(
            "return typeof jsDebugCurrentStatsClientState?.client?.stop === 'function';"
        ),
        message="editor/differ fixture stats client did not finish initializing",
    )
    stats_client_stopped = browser.execute_script(
        """
        if (typeof jsDebugCurrentStatsClientState === 'undefined') return false;
        const client = jsDebugCurrentStatsClientState?.client;
        if (typeof client?.stop !== 'function') return false;
        client.stop();
        return true;
        """
    )
    assert stats_client_stopped is True, "editor/differ fixture could not retire its unrelated stats client"
    try:
        yield SimpleNamespace(browser=browser, runtime=runtime, server=server, session=session)
    finally:
        stop_browser_server(server, thread, browser=browser)
        stop_isolated_browser_app(runtime)


def _open_editor(gate_browser_runtime, target, expected):
    browser = gate_browser_runtime.browser
    metrics = browser.execute_async_script(
        """
        const path = arguments[0];
        const expected = arguments[1];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const item = await openFileInEditor(path, {name: path.split('/').at(-1)}, {userInitiated: true, viewMode: 'edit'});
            const ready = await window.__yolomuxTestWaitFor(() => {
              const panel = fileEditorPanelsForPath(path)[0];
              return panel?._cmView?.state?.doc?.toString?.() === expected && Boolean(panel.querySelector('.cm-content'));
            }, {timeoutMs: 10000, description: `CodeMirror content for ${path}`});
            const panel = fileEditorPanelsForPath(path)[0];
            const tab = document.querySelector(`.dockview-pane-tab[data-pane-tab="${CSS.escape(item)}"]`);
            done({
              ready,
              item,
              text: panel?._cmView?.state?.doc?.toString?.() || '',
              path: panel ? fileEditorPanelPath(panel) : '',
              tabConnected: tab?.isConnected === true,
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
            });
          } catch (error) {
            done({error: String(error?.stack || error), errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          }
        })();
        """,
        str(target),
        expected,
    )
    assert not metrics.get("error"), metrics
    assert metrics["ready"] is True, metrics
    assert metrics["text"] == expected and metrics["text"], metrics
    assert metrics["path"] == str(target), metrics
    assert metrics["tabConnected"] is True, metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics
    return metrics


def _wait_for_active_watchd_descriptor(gate_browser_runtime, target, descriptor_field):
    app = gate_browser_runtime.runtime.app
    target_path = str(target.resolve(strict=False))
    last_state = {}

    def watchd_ready(_driver):
        with app.client_watch_service.lock:
            record = app.client_watch_service.event_watcher_record
            matching_descriptors = {
                descriptor_id: descriptor.descriptor_generation
                for descriptor_id, descriptor in app.client_watch_service.descriptors.items()
                if target_path in getattr(descriptor, descriptor_field)
            }
            acknowledged_descriptors = {
                descriptor_id: record.watchd_descriptor_generations.get(descriptor_id)
                for descriptor_id in matching_descriptors
            }
            exact_descriptor_active = any(
                acknowledged_descriptors[descriptor_id] == descriptor_generation
                for descriptor_id, descriptor_generation in matching_descriptors.items()
            )
            last_state.clear()
            last_state.update({
                "target": target_path,
                "descriptor_field": descriptor_field,
                "matching_descriptor_generations": matching_descriptors,
                "acknowledged_descriptor_generations": acknowledged_descriptors,
                "requested_generation": record.watchd_synced_generation,
                "applied_generation": record.watchd_applied_generation,
                "active_generation": record.watchd_active_generation,
                "epoch": record.watchd_epoch,
                "revision": record.watchd_revision,
                "state": record.watchd_state,
                "healthy": record.filesystem_healthy,
                "lease_id_present": bool(record.watchd_lease_id),
                "descriptor_ids": sorted(record.watchd_descriptor_ids),
                "failure_action": record.watchd_failure_action,
                "failure_error_code": record.watchd_failure_error_code,
                "failure_count": record.watchd_failure_count,
            })
            if (
                record.watchd_worker is None
                or not record.watchd_lease_id
                or not record.watchd_descriptor_ids
                or not record.watchd_epoch
                or not record.filesystem_healthy
                or record.watchd_state not in {"ready", "polling"}
                or record.watchd_synced_generation <= 0
                or record.watchd_applied_generation < record.watchd_synced_generation
                or record.watchd_active_generation < record.watchd_synced_generation
                or not exact_descriptor_active
            ):
                return False
            latest = app.client_watch_service.filesystem_history[-1] if app.client_watch_service.filesystem_history else {}
            return {
                "epoch": record.watchd_epoch,
                "revision": record.watchd_revision,
                "token": str(latest.get("token") or ""),
                "watch_generation": record.watchd_applied_generation,
                "active_watch_generation": record.watchd_active_generation,
            }

    try:
        return WebDriverWait(gate_browser_runtime.browser, 10).until(watchd_ready)
    except TimeoutException as error:
        daemon_status = app.watch_client.runtime_status()
        last_state["daemon_error"] = daemon_status.get("last_failure")
        last_state["daemon_fallback"] = daemon_status.get("fallback")
        raise AssertionError(f"watchd descriptor did not become active: {last_state}") from error


def _wait_for_watchd_path_change(gate_browser_runtime, baseline, target, history_field):
    app = gate_browser_runtime.runtime.app
    target_path = str(target.resolve(strict=False))
    last_state = {}

    def watchd_changed(_driver):
        with app.client_watch_service.lock:
            record = app.client_watch_service.event_watcher_record
            latest = app.client_watch_service.filesystem_history[-1] if app.client_watch_service.filesystem_history else {}
            last_state.update({
                "filesystem_healthy": record.filesystem_healthy,
                "state": record.watchd_state,
                "epoch": record.watchd_epoch,
                "revision": record.watchd_revision,
                "synced_generation": record.watchd_synced_generation,
                "applied_generation": record.watchd_applied_generation,
                "active_generation": record.watchd_active_generation,
                "failure_action": record.watchd_failure_action,
                "failure_error_code": record.watchd_failure_error_code,
                "failure_count": record.watchd_failure_count,
                "latest_epoch": str(latest.get("watchd_epoch") or ""),
                "latest_revision": int(latest.get("watchd_revision") or 0),
                "latest_generation": int(latest.get("watch_generation") or 0),
                "latest_active_generation": int(latest.get("active_watch_generation") or 0),
                "latest_changed_paths": tuple(latest.get("changed_paths") or ()),
                "latest_files_changed": tuple(
                    item.get("path")
                    for item in latest.get("files_changed", [])
                    if isinstance(item, dict)
                ),
            })
            if not record.filesystem_healthy or record.watchd_state not in {"ready", "polling"}:
                return False
            for history_record in reversed(app.client_watch_service.filesystem_history):
                epoch = str(history_record.get("watchd_epoch") or "")
                revision = int(history_record.get("watchd_revision") or 0)
                if epoch != baseline["epoch"] or revision <= baseline["revision"]:
                    continue
                values = history_record.get(history_field)
                if history_field == "files_changed" and isinstance(values, list):
                    matched = any(item.get("path") == target_path for item in values if isinstance(item, dict))
                else:
                    matched = isinstance(values, (list, tuple)) and target_path in values
                if matched:
                    return {
                        "epoch": epoch,
                        "revision": revision,
                        "token": str(history_record.get("token") or ""),
                        "watch_generation": int(history_record.get("watch_generation") or 0),
                        "active_watch_generation": int(history_record.get("active_watch_generation") or 0),
                    }
            return False

    try:
        return WebDriverWait(gate_browser_runtime.browser, 10).until(
            watchd_changed,
            message=f"real watchd {history_field} revision for {target}",
        )
    except TimeoutException as error:
        daemon_status = app.watch_client.runtime_status()
        last_state["daemon_error"] = daemon_status.get("last_failure")
        last_state["daemon_fallback"] = daemon_status.get("fallback")
        raise AssertionError(
            f"real watchd {history_field} revision missing for {target}: {last_state}"
        ) from error


def _wait_for_file_event_stream(gate_browser_runtime, target):
    result = gate_browser_runtime.browser.execute_async_script(
        """
        const path = arguments[0];
        const done = arguments[arguments.length - 1];
        const previousSyncedAt = Number(serverWatchRootsState.syncedAt || 0);
        syncServerWatchRoots({immediate: true, force: true});
        window.__yolomuxTestWaitFor(() => {
          const descriptor = clientEventDemandDescriptor();
          return descriptor.channels.includes('files')
            && clientEventTransportState.connected === true
            && Number(serverWatchRootsState.syncedAt || 0) > previousSyncedAt
            && serverWatchRootsState.inFlight !== true
            && visibleFileEditorWatchFiles().includes(path);
        }, {timeoutMs: 10000, description: `files SSE registration for ${path}`}).then(done, error => done({error: String(error?.stack || error)}));
        """,
        str(target),
    )
    assert result is True, result

    gate_browser_runtime.file_event_watchd = _wait_for_active_watchd_descriptor(
        gate_browser_runtime,
        target,
        "files",
    )


def _publish_file_change(gate_browser_runtime, target):
    baseline = gate_browser_runtime.file_event_watchd
    gate_browser_runtime.file_event_watchd = _wait_for_watchd_path_change(
        gate_browser_runtime,
        baseline,
        target,
        "files_changed",
    )


def _type_dirty_text(gate_browser_runtime, target, text):
    browser = gate_browser_runtime.browser
    panel_selector = f'.file-editor-panel[data-file-path="{str(target)}"]'
    autosave = browser.execute_script(
        """
        const path = arguments[0];
        fileEditorAutosaveEnabled = false;
        rescheduleAllFileAutosaves();
        return {
          enabled: fileEditorAutosaveEnabled,
          pending: fileEditorAutosaveTimers.has(path),
        };
        """,
        str(target),
    )
    assert autosave == {"enabled": False, "pending": False}, autosave
    content = browser.find_element("css selector", f"{panel_selector} .cm-content")
    content.click()
    content.send_keys("\ue010", "\ue007", text)
    result = browser.execute_async_script(
        """
        const path = arguments[0];
        const text = arguments[1];
        const done = arguments[arguments.length - 1];
        window.__yolomuxTestWaitFor(() => {
          const state = fileState.get(path);
          const panel = fileEditorPanelsForPath(path)[0];
          return state?.dirty === true && panel?._cmView?.state?.doc?.toString?.().includes(text);
        }, {timeoutMs: 5000, description: `dirty CodeMirror buffer for ${path}`}).then(() => {
          const state = fileState.get(path);
          const panel = fileEditorPanelsForPath(path)[0];
          done({
            content: state?.content || '',
            text: panel?._cmView?.state?.doc?.toString?.() || '',
            dirty: state?.dirty === true,
            autosaveEnabled: fileEditorAutosaveEnabled,
            autosavePending: fileEditorAutosaveTimers.has(path),
          });
        }, error => done({error: String(error?.stack || error)}));
        """,
        str(target),
        text,
    )
    assert not result.get("error"), result
    assert result["dirty"] is True and text in result["text"], result
    assert result["autosaveEnabled"] is False and result["autosavePending"] is False, result
    return result["text"]


def _dirty_conflict_snapshot(gate_browser_runtime, target, expected_text, previous_signature=""):
    return gate_browser_runtime.browser.execute_async_script(
        """
        const path = arguments[0];
        const expected = arguments[1];
        const previousSignature = arguments[2];
        const done = arguments[arguments.length - 1];
        const signature = state => JSON.stringify(state?.externalChanged || {});
        window.__yolomuxTestWaitFor(() => {
          const state = fileState.get(path);
          const panel = fileEditorPanelsForPath(path)[0];
          const status = panel?.querySelector('.file-editor-status-message');
          return state?.dirty === true
            && Boolean(state.externalChanged)
            && (!previousSignature || signature(state) !== previousSignature)
            && panel?._cmView?.state?.doc?.toString?.() === expected
            && status?.textContent?.trim()
            && getComputedStyle(status).display !== 'none';
        }, {timeoutMs: 10000, description: `visible external-change conflict for ${path}`}).then(() => {
          const state = fileState.get(path);
          const panel = fileEditorPanelsForPath(path)[0];
          const item = panel?.dataset?.layoutItem || '';
          const status = panel?.querySelector('.file-editor-status-message');
          done({
            dirty: state?.dirty === true,
            text: panel?._cmView?.state?.doc?.toString?.() || '',
            signature: signature(state),
            path: panel ? fileEditorPanelPath(panel) : '',
            item,
            tabConnected: Boolean(item && document.querySelector(`.dockview-pane-tab[data-pane-tab="${CSS.escape(item)}"]`)),
            missing: state?.kind === 'missing' || state?.externalMissing === true,
            status: status?.textContent?.trim() || '',
            statusDisplay: status ? getComputedStyle(status).display : 'none',
            body: document.body.innerText,
          });
        }, error => done({error: String(error?.stack || error)}));
        """,
        str(target),
        expected_text,
        previous_signature,
    )


A8_FRAME_COUNTS = (1, 3, 2, 5, 1, 4, 2, 6, 3, 1)


def _a8_wait_varying_subsecond_interval(gate_browser_runtime, iteration):
    frame_count = A8_FRAME_COUNTS[iteration - 1]
    result = gate_browser_runtime.browser.execute_async_script(
        """
        let remaining = arguments[0];
        const done = arguments[arguments.length - 1];
        const next = () => {
          remaining -= 1;
          if (remaining <= 0) done(true);
          else requestAnimationFrame(next);
        };
        requestAnimationFrame(next);
        """,
        frame_count,
    )
    assert result is True


def _a8_replace_inode(target, text, iteration):
    replacement = target.with_name(f".{target.name}.a8-{iteration}.tmp")
    replacement.write_text(text, encoding="utf-8")
    inode_before = target.stat().st_ino
    started = time.perf_counter()
    os.replace(replacement, target)
    elapsed = time.perf_counter() - started
    inode_after = target.stat().st_ino
    assert inode_after != inode_before, f"replace {iteration} did not swap the inode"
    assert elapsed < 1.0, f"replace {iteration} took {elapsed:.3f}s"
    return elapsed


def _a8_latch_transient_inode_miss(gate_browser_runtime, target):
    """Deliver the old-inode delete/move signal while the replacement path is resolvable."""
    assert target.is_file(), f"replacement path vanished before transient miss: {target}"
    metrics = gate_browser_runtime.browser.execute_script(
        """
        const path = arguments[0];
        markOpenFileMissing(path);
        const state = fileState.get(path);
        const panel = fileEditorPanelsForPath(path)[0];
        const item = panel?.dataset?.layoutItem || fileEditorItemFor(path);
        const tab = document.querySelector(`.dockview-pane-tab[data-pane-tab="${CSS.escape(item)}"]`);
        return {
          missing: state?.externalMissing === true,
          tabMissing: tab?.classList.contains('file-missing') === true,
          missingBadge: Boolean(tab?.querySelector('.file-tab-missing-badge')),
        };
        """,
        str(target),
    )
    assert metrics == {"missing": True, "tabMissing": True, "missingBadge": True}, metrics


def _a8_recovered_snapshot(gate_browser_runtime, target, expected_text, expected_view_mode="edit"):
    return gate_browser_runtime.browser.execute_async_script(
        """
        const path = arguments[0];
        const expected = arguments[1];
        const expectedViewMode = arguments[2];
        const done = arguments[arguments.length - 1];
        window.__yolomuxTestWaitFor(() => {
          const state = fileState.get(path);
          const panel = fileEditorPanelsForPath(path)[0];
          const item = panel?.dataset?.layoutItem || fileEditorItemFor(path);
          const tab = document.querySelector(`.dockview-pane-tab[data-pane-tab="${CSS.escape(item)}"]`);
          const text = panel?._cmView?.state?.doc?.toString?.() || '';
          return state?.externalMissing !== true
            && state?.kind === 'text'
            && text === expected
            && tab?.isConnected === true
            && !tab.classList.contains('file-missing')
            && !tab.querySelector('.file-tab-missing-badge')
            && editorViewModeFor(path, item) === expectedViewMode;
        }, {timeoutMs: 10000, description: `A8 inode-swap recovery for ${path}`}).then(() => {
          const state = fileState.get(path);
          const panel = fileEditorPanelsForPath(path)[0];
          const item = panel?.dataset?.layoutItem || fileEditorItemFor(path);
          const tab = document.querySelector(`.dockview-pane-tab[data-pane-tab="${CSS.escape(item)}"]`);
          done({
            text: panel?._cmView?.state?.doc?.toString?.() || '',
            missing: state?.externalMissing === true,
            tabMissing: tab?.classList.contains('file-missing') === true,
            missingBadge: Boolean(tab?.querySelector('.file-tab-missing-badge')),
            viewMode: editorViewModeFor(path, item),
            errors: jsDebugFailureEvents('error'),
            rejections: jsDebugFailureEvents('rejection'),
          });
        }, error => done({error: String(error?.stack || error)}));
        """,
        str(target),
        expected_text,
        expected_view_mode,
    )


def _a8_missing_snapshot(gate_browser_runtime, target):
    return gate_browser_runtime.browser.execute_async_script(
        """
        const path = arguments[0];
        const done = arguments[arguments.length - 1];
        window.__yolomuxTestWaitFor(() => {
          const state = fileState.get(path);
          const panel = fileEditorPanelsForPath(path)[0];
          const item = panel?.dataset?.layoutItem || fileEditorItemFor(path);
          const tab = document.querySelector(`.dockview-pane-tab[data-pane-tab="${CSS.escape(item)}"]`);
          const status = panel?.querySelector('.file-editor-status-message');
          return state?.externalMissing === true
            && tab?.classList.contains('file-missing') === true
            && Boolean(tab.querySelector('.file-tab-missing-badge'))
            && Boolean(status?.textContent?.trim());
        }, {timeoutMs: 10000, description: `A8 genuine missing state for ${path}`}).then(() => {
          const state = fileState.get(path);
          const panel = fileEditorPanelsForPath(path)[0];
          const item = panel?.dataset?.layoutItem || fileEditorItemFor(path);
          const tab = document.querySelector(`.dockview-pane-tab[data-pane-tab="${CSS.escape(item)}"]`);
          done({
            missing: state?.externalMissing === true,
            tabMissing: tab?.classList.contains('file-missing') === true,
            missingBadge: Boolean(tab?.querySelector('.file-tab-missing-badge')),
            status: panel?.querySelector('.file-editor-status-message')?.textContent?.trim() || '',
            text: panel?._cmView?.state?.doc?.toString?.() || '',
            rendered: [...(panel?.querySelectorAll('.cm-content') || [])].map(node => node.textContent || '').join('\\n'),
          });
        }, error => done({error: String(error?.stack || error)}));
        """,
        str(target),
    )
