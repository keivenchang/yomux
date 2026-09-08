# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Acceptance coverage for the reusable real-page browser harness."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
from typing import Any

import pytest
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from tests.browser_helpers.browser_console import assert_browser_journey_error_free
from tests.browser_helpers.browser_console import acknowledge_and_consume_only_expected_js_debug_failures
from tests.browser_helpers.browser_console import acknowledge_browser_diagnostic_receipts
from tests.browser_helpers.browser_console import begin_browser_journey_surface_tracking
from tests.browser_helpers.browser_console import emit_js_debug_event
from tests.browser_helpers.browser_layout import WebDriverWait
from tests.gate_harness import wait_for_browser_boot
from tests.gate_harness import retire_expected_fixture_server_log_errors
from tests.tmux_runtime import run_isolated_tmux
from yolomux_lib.server_logs import SERVER_LOGS


pytest_plugins = ("tests.e2e_browser_harness",)
pytestmark = [pytest.mark.browser, pytest.mark.socket, pytest.mark.e2e]


def _make_finder_repo(
    harness: Any,
    name: str,
    branch: str,
    children: tuple[str, ...],
) -> tuple[Path, Path]:
    repo = harness.runtime.paths.home_dir / "dev" / name
    for child in children:
        (repo / child).mkdir(parents=True)
    subprocess.run(
        ("git", "init", "-q", "-b", branch, str(repo)),
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return repo, repo / children[0]


def _make_finder_repo_tree(harness: Any) -> tuple[Path, Path, Path]:
    repo, child = _make_finder_repo(
        harness,
        "ai-config",
        "master",
        ("assets", "backend", "frontend", "run", "tools"),
    )
    return repo.parent, repo, child


def _differ_payload(harness: Any, repo: Path) -> tuple[dict[str, object], Path]:
    target = repo / "changed.txt"
    target.write_text("fixture-driven Differ content\n", encoding="utf-8")
    stat = target.stat()
    session = str(harness.runtime.tmux.sessions[0])
    return {
        "session": session,
        "loaded": True,
        "errors": [],
        "refs_by_repo": {str(repo): {"from_ref": "HEAD", "to_ref": "current"}},
        "repos": [{"repo": str(repo), "count": 1}],
        "files": [{
            "session": session,
            "agent": "codex",
            "status": "M",
            "repo": str(repo),
            "path": target.name,
            "abs_path": str(target),
            "mtime": stat.st_mtime,
            "size": stat.st_size,
            "added": 1,
            "removed": 0,
        }],
    }, target


def test_real_page_harness_owns_7900s_runtime_and_user_action_helpers(e2e_browser: Any) -> None:
    dev, repo, child = _make_finder_repo_tree(e2e_browser)
    url = e2e_browser.load()

    assert url.startswith(f"http://127.0.0.1:{e2e_browser.runtime.port}/")
    assert 7900 <= e2e_browser.runtime.port <= 7999
    assert e2e_browser.runtime.paths.root in dev.parents

    e2e_browser.expand(dev, child_path=repo)

    def exercise_repo() -> dict[str, object]:
        e2e_browser.re_expand(repo, child_path=child)
        pending = e2e_browser.assert_no_pending_indicator(repo)
        return {"pending": pending, "child": e2e_browser.read_rendered_dom(e2e_browser.finder_row(child))}

    repeated = e2e_browser.assert_repeated(exercise_repo)
    assert len(repeated) == 5
    assert all(item["child"]["connected"] and "assets" in item["child"]["text"] for item in repeated)

    session = str(e2e_browser.runtime.tmux.sessions[0])
    panel = e2e_browser.switch_session(session)
    session_state = e2e_browser.assert_reaches_terminal_state(panel, bound=12)
    assert session_state["terminal"] is True


def test_real_chromium_markdown_split_native_sync_and_breaks(e2e_browser: Any) -> None:
    """Drive the actual page and both editor surfaces with native Chromium input."""

    target = e2e_browser.runtime.paths.home_dir / "dev" / "markdown-e2e.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("helloworld\n", encoding="utf-8")
    e2e_browser.load(tabs=("files",))
    wait_for_browser_boot(
        e2e_browser.driver,
        globals_required={
            "openFileInEditor": "function",
            "fileEditorItemFor": "function",
            "fileEditorPanelState": "function",
        },
        dom_anchors=("#grid",),
        timeout=12,
    )

    opened = e2e_browser.driver.execute_async_script(
        """
        const path = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const item = await openFileInEditor(
              path,
              {name: 'markdown-e2e.md'},
              {userInitiated: true, viewMode: 'split'},
            );
            const panel = await window.__yolomuxTestWaitFor(() => {
              const candidate = [...document.querySelectorAll('.file-editor-panel')]
                .find(node => node.isConnected && node.dataset.filePath === path);
              return candidate?._cmView && candidate?._pmView
                && candidate.querySelector('[data-editor-surface="text-editor"] .cm-content')
                && candidate.querySelector('[data-editor-surface="view-editor"] .ProseMirror')
                && candidate.querySelectorAll('[data-editor-surface="view-editor"] .ProseMirror').length === 1
                ? candidate : null;
            }, {timeoutMs: 15000, description: 'real Chromium Markdown Split editor'});
            done({
              item,
              path: panel.dataset.filePath,
              mode: editorViewModeFor(path, item),
              textLabel: panel.querySelector('[data-editor-surface="text-editor"]')?.getAttribute('aria-label') || '',
              viewLabel: panel.querySelector('[data-editor-surface="view-editor"]')?.getAttribute('aria-label') || '',
            });
          } catch (error) {
            done({error: String(error?.stack || error)});
          }
        })();
        """
        ,
        str(target),
    )
    assert "error" not in opened, opened
    assert opened["path"] == str(target), opened
    assert opened["mode"] == "split", opened
    assert opened["textLabel"] == "TextEditor", opened
    assert opened["viewLabel"] == "ViewEditor", opened

    panel = WebDriverWait(e2e_browser.driver, 15).until(
        lambda driver: driver.find_element(
            By.CSS_SELECTOR,
            f'.file-editor-panel[data-file-path="{str(target).replace(chr(34), "\\\\\\\"")}"]',
        )
    )
    view = panel.find_element(By.CSS_SELECTOR, '[data-editor-surface="view-editor"] .ProseMirror')
    view.click()
    view.send_keys("typed")
    immediate_view_state = e2e_browser.driver.execute_script(
        "const p=arguments[0]; return {source:fileEditorPanelState(p)?.content || '', cm:p._cmView?.state.doc.toString() || '', pm:p._pmView?.state.doc.textContent || ''};",
        panel,
    )
    assert immediate_view_state["source"] == "helloworld\n", immediate_view_state
    assert immediate_view_state["cm"] == "helloworld\n", immediate_view_state
    assert "typed" in immediate_view_state["pm"], immediate_view_state

    view_state = WebDriverWait(e2e_browser.driver, 12).until(
        lambda driver: driver.execute_script(
            """
            const path = arguments[0];
            const panel = [...document.querySelectorAll('.file-editor-panel')]
              .find(node => node.isConnected && node.dataset.filePath === path);
            const source = String(fileEditorPanelState(panel)?.content || '');
            const cm = panel?._cmView?.state?.doc?.toString?.() || '';
            const pm = panel?._pmView?.state?.doc?.textContent || '';
            return source.includes('typed') && cm === source && pm.includes('typed')
              ? {source, cm, pm, active: document.activeElement === panel._pmView.dom}
              : false;
            """,
            str(target),
        )
    )
    assert "<br>" not in view_state["source"], view_state
    assert "typed" in view_state["source"], view_state

    text = panel.find_element(By.CSS_SELECTOR, '[data-editor-surface="text-editor"] .cm-content')
    text.click()
    text.send_keys(Keys.END, " source")
    immediate_text_state = e2e_browser.driver.execute_script(
        "const p=arguments[0]; return {source:fileEditorPanelState(p)?.content || '', cm:p._cmView?.state.doc.toString() || '', pm:p._pmView?.state.doc.textContent || ''};",
        panel,
    )
    assert immediate_text_state["source"] == view_state["source"], immediate_text_state
    assert immediate_text_state["pm"] == view_state["pm"], immediate_text_state
    assert immediate_text_state["cm"].endswith(" source"), immediate_text_state
    text_state = WebDriverWait(e2e_browser.driver, 12).until(
        lambda driver: driver.execute_script(
            """
            const path = arguments[0];
            const panel = [...document.querySelectorAll('.file-editor-panel')]
              .find(node => node.isConnected && node.dataset.filePath === path);
            const source = String(fileEditorPanelState(panel)?.content || '');
            const pm = panel?._pmView?.state?.doc?.textContent || '';
            const pmJson = panel?._pmView?.state?.doc?.toJSON?.() || null;
            return source.endsWith(' source') && pm.includes('source')
              ? {source, pm, pmJson, connected: panel._pmView.dom.isConnected}
              : false;
            """,
            str(target),
        )
    )
    assert text_state["connected"] is True, text_state
    assert text_state["source"].endswith("typed source"), text_state
    assert text_state["pm"].endswith("typed source"), text_state
    assert_browser_journey_error_free(e2e_browser.driver, server_log_boundary=e2e_browser.runtime.server_log_boundary)


def test_real_chromium_markdown_vieweditor_double_enter_never_writes_backslash(e2e_browser: Any) -> None:
    """Ordinary Enter creates Markdown paragraphs; only Shift-Enter may create a hard break."""

    target = e2e_browser.runtime.paths.home_dir / "dev" / "markdown-double-enter.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("before", encoding="utf-8")
    e2e_browser.load(tabs=("files",))
    wait_for_browser_boot(
        e2e_browser.driver,
        globals_required={"openFileInEditor": "function", "fileEditorPanelState": "function"},
        dom_anchors=("#grid",),
        timeout=12,
    )
    opened = e2e_browser.driver.execute_async_script(
        """
        const path = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            await openFileInEditor(path, {name: 'markdown-double-enter.md'}, {userInitiated: true, viewMode: 'split'});
            await window.__yolomuxTestWaitFor(() => [...document.querySelectorAll('.file-editor-panel')]
              .find(node => node.isConnected && node.dataset.filePath === path && node._pmView && node._cmView),
              {timeoutMs: 15000, description: 'double Enter ViewEditor'});
            done(true);
          } catch (error) {
            done({error: String(error?.stack || error)});
          }
        })();
        """,
        str(target),
    )
    assert opened is True, opened
    panel = WebDriverWait(e2e_browser.driver, 15).until(
        lambda driver: driver.find_element(By.CSS_SELECTOR, f'.file-editor-panel[data-file-path="{str(target)}"]')
    )
    view = panel.find_element(By.CSS_SELECTOR, '[data-editor-surface="view-editor"] .ProseMirror')
    view.click()
    view.send_keys(Keys.END, Keys.ENTER, Keys.ENTER, "after")
    settled = WebDriverWait(e2e_browser.driver, 12).until(
        lambda driver: driver.execute_script(
            """
            const p = arguments[0];
            const source = fileEditorPanelState(p)?.content || '';
            return source.includes('after')
              ? {source, cm: p._cmView?.state.doc.toString() || '', pm: p._pmView?.state.doc.toJSON() || null}
              : false;
            """,
            panel,
        )
    )
    assert "\\\n" not in settled["source"], settled
    assert settled["source"] == "before\n\n\nafter", settled
    assert settled["cm"] == settled["source"], settled
    assert [node["type"] for node in settled["pm"]["content"]] == ["paragraph", "paragraph", "paragraph"], settled
    view.send_keys(Keys.END, Keys.ENTER, Keys.ENTER)
    trailing = WebDriverWait(e2e_browser.driver, 12).until(
        lambda driver: driver.execute_script(
            "const p=arguments[0], source=fileEditorPanelState(p)?.content || ''; return source.endsWith('after\\n\\n\\n') ? {source, pm:p._pmView?.state.doc.toJSON() || null} : false;",
            panel,
        )
    )
    assert "\\\n" not in trailing["source"], trailing
    assert trailing["source"].endswith("after\n\n\n"), trailing
    assert [node["type"] for node in trailing["pm"]["content"]][-2:] == ["paragraph", "paragraph"], trailing
    assert_browser_journey_error_free(e2e_browser.driver, server_log_boundary=e2e_browser.runtime.server_log_boundary)


def test_real_chromium_markdown_split_keeps_each_surface_local_until_idle(e2e_browser: Any) -> None:
    """Every native key stays on its editing surface until the trailing idle commit."""

    target = e2e_browser.runtime.paths.home_dir / "dev" / "markdown-idle-sync.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("444", encoding="utf-8")
    e2e_browser.load(tabs=("files",))
    wait_for_browser_boot(
        e2e_browser.driver,
        globals_required={"openFileInEditor": "function", "fileEditorPanelState": "function"},
        dom_anchors=("#grid",),
        timeout=12,
    )
    opened = e2e_browser.driver.execute_async_script(
        """
        const path = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            await openFileInEditor(path, {name: 'markdown-idle-sync.md'}, {userInitiated: true, viewMode: 'split'});
            const panel = await window.__yolomuxTestWaitFor(() => [...document.querySelectorAll('.file-editor-panel')]
              .find(node => node.isConnected && node.dataset.filePath === path && node._cmView && node._pmView),
              {timeoutMs: 15000, description: 'idle synchronization editor'});
            done(true);
          } catch (error) {
            done({error: String(error?.stack || error)});
          }
        })();
        """,
        str(target),
    )
    assert opened is True, opened
    panel = WebDriverWait(e2e_browser.driver, 15).until(
        lambda driver: driver.find_element(By.CSS_SELECTOR, f'.file-editor-panel[data-file-path="{str(target)}"]')
    )
    view = panel.find_element(By.CSS_SELECTOR, '[data-editor-surface="view-editor"] .ProseMirror')
    view.click()
    view.send_keys("a")
    first_view_key = e2e_browser.driver.execute_script(
        "const p=arguments[0]; return {pm:p._pmView.state.doc.textContent, cm:p._cmView.state.doc.toString(), source:fileEditorPanelState(p).content};",
        panel,
    )
    assert first_view_key == {"pm": "444a", "cm": "444", "source": "444"}, first_view_key
    view.send_keys("b")
    second_view_key = e2e_browser.driver.execute_script(
        "const p=arguments[0]; return {pm:p._pmView.state.doc.textContent, cm:p._cmView.state.doc.toString(), source:fileEditorPanelState(p).content};",
        panel,
    )
    assert second_view_key == {"pm": "444ab", "cm": "444", "source": "444"}, second_view_key
    time.sleep(2.4)
    view_idle = e2e_browser.driver.execute_script(
        "const p=arguments[0]; return {pm:p._pmView.state.doc.textContent, cm:p._cmView.state.doc.toString(), source:fileEditorPanelState(p).content};",
        panel,
    )
    assert view_idle == {"pm": "444ab", "cm": "444ab", "source": "444ab"}, view_idle

    text = panel.find_element(By.CSS_SELECTOR, '[data-editor-surface="text-editor"] .cm-content')
    text.click()
    text.send_keys("c")
    first_text_key = e2e_browser.driver.execute_script(
        "const p=arguments[0]; return {pm:p._pmView.state.doc.textContent, cm:p._cmView.state.doc.toString(), source:fileEditorPanelState(p).content};",
        panel,
    )
    assert first_text_key == {"pm": "444ab", "cm": "444abc", "source": "444ab"}, first_text_key
    text.send_keys("d")
    second_text_key = e2e_browser.driver.execute_script(
        "const p=arguments[0]; return {pm:p._pmView.state.doc.textContent, cm:p._cmView.state.doc.toString(), source:fileEditorPanelState(p).content};",
        panel,
    )
    assert second_text_key == {"pm": "444ab", "cm": "444abcd", "source": "444ab"}, second_text_key
    time.sleep(2.4)
    text_idle = e2e_browser.driver.execute_script(
        "const p=arguments[0]; return {pm:p._pmView.state.doc.textContent, cm:p._cmView.state.doc.toString(), source:fileEditorPanelState(p).content};",
        panel,
    )
    assert text_idle == {"pm": "444abcd", "cm": "444abcd", "source": "444abcd"}, text_idle
    assert_browser_journey_error_free(e2e_browser.driver, server_log_boundary=e2e_browser.runtime.server_log_boundary)


def test_real_chromium_markdown_view_shows_prosemirror_failure_without_legacy_preview(e2e_browser: Any) -> None:
    """A ProseMirror parse failure is an explicit ViewEditor error, never an editable legacy preview."""

    target = e2e_browser.runtime.paths.home_dir / "dev" / "markdown-preview-fallback.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# Direct Editing In Preview\n\n"
        "***~~<span class=\"unsupported\">hello</span>~~***\n\n"
        "| Preview type | Decision |\n"
        "| --- | --- |\n"
        "| Markdown | Keep rendered |\n\n"
        + "\n\n".join(f"## Section {index}\n\nRendered paragraph {index}." for index in range(80))
        + "\n",
        encoding="utf-8",
    )
    e2e_browser.load(tabs=("files",))
    wait_for_browser_boot(
        e2e_browser.driver,
        globals_required={"openFileInEditor": "function", "fileEditorPanelState": "function"},
        dom_anchors=("#grid",),
        timeout=12,
    )
    metrics = e2e_browser.driver.execute_async_script(
        """
        const path = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            await openFileInEditor(path, {name: 'markdown-preview-fallback.md'}, {userInitiated: true, viewMode: 'preview'});
            const panel = await window.__yolomuxTestWaitFor(() => [...document.querySelectorAll('.file-editor-panel')]
              .find(node => node.isConnected && node.dataset.filePath === path),
              {timeoutMs: 12000, description: 'complex Markdown ViewEditor panel'});
            const samples = [];
            for (const delay of [0, 100, 300, 700]) {
              await new Promise(resolve => setTimeout(resolve, delay));
              const pane = panel.querySelector('[data-editor-surface="view-editor"]');
              samples.push({
                text: pane?.textContent || '',
                error: pane?.querySelector('.file-editor-prosemirror-error')?.textContent || '',
                errorBox: pane?.querySelector('.file-editor-prosemirror-error')?.getBoundingClientRect().toJSON() || null,
                prosemirrorRoots: pane?.querySelectorAll('.ProseMirror').length || 0,
                legacyEditable: pane?.querySelectorAll('[data-markdown-preview-editable="true"]').length || 0,
                state: pane?.dataset.prosemirrorState || '',
              });
            }
            done({samples, error: panel._pmError || '', status: document.getElementById('status')?.textContent || ''});
          } catch (error) {
            done({failure: String(error?.stack || error)});
          }
        })();
        """,
        str(target),
    )
    assert "failure" not in metrics, metrics
    assert "ProseMirror ViewEditor failed: Unsupported raw HTML at line 3: <span class=\"unsupported\">" in metrics["error"], metrics
    assert "ProseMirror ViewEditor failed" in metrics["status"], metrics
    assert any("ProseMirror ViewEditor failed" in sample["error"] and "Direct Editing In Preview" in sample["text"] for sample in metrics["samples"]), metrics
    terminal = metrics["samples"][-1]
    assert terminal["state"] == "error" and terminal["prosemirrorRoots"] == 0 and terminal["legacyEditable"] == 0, metrics
    assert terminal["errorBox"]["height"] >= 180 and "Direct Editing In Preview" in terminal["text"], metrics
    assert_browser_journey_error_free(e2e_browser.driver, server_log_boundary=e2e_browser.runtime.server_log_boundary)


def test_real_chromium_markdown_split_full_vieweditor_contract(e2e_browser: Any) -> None:
    """Cover the complete user-reported TextEditor/ViewEditor contract in real Chromium."""

    target = e2e_browser.runtime.paths.home_dir / "dev" / "markdown-full-e2e.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("helloworld\n", encoding="utf-8")
    e2e_browser.load(tabs=("files",))
    wait_for_browser_boot(
        e2e_browser.driver,
        globals_required={"openFileInEditor": "function", "fileEditorPanelState": "function"},
        dom_anchors=("#grid",),
        timeout=12,
    )
    opened = e2e_browser.driver.execute_async_script(
        """
        const path = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            await openFileInEditor(path, {name: 'markdown-full-e2e.md'}, {userInitiated: true, viewMode: 'split'});
            const panel = await window.__yolomuxTestWaitFor(() => {
              const candidate = [...document.querySelectorAll('.file-editor-panel')]
                .find(node => node.isConnected && node.dataset.filePath === path);
              return candidate?._cmView && candidate?._pmView
                && candidate.querySelector('[data-editor-surface="text-editor"] .cm-content')
                && candidate.querySelector('[data-editor-surface="view-editor"] .ProseMirror')
                && candidate.querySelectorAll('[data-editor-surface="view-editor"] .ProseMirror').length === 1
                ? candidate : null;
            }, {timeoutMs: 15000, description: 'full real Chromium Split contract'});
            done({path: panel.dataset.filePath, mode: editorViewModeFor(path, fileEditorItemFor(path)), textLabel: panel.querySelector('[data-editor-surface="text-editor"]')?.getAttribute('aria-label'), viewLabel: panel.querySelector('[data-editor-surface="view-editor"]')?.getAttribute('aria-label')});
          } catch (error) { done({error: String(error?.stack || error)}); }
        })();
        """
        ,
        str(target),
    )
    assert "error" not in opened, opened
    assert opened["mode"] == "split" and opened["textLabel"] == "TextEditor" and opened["viewLabel"] == "ViewEditor", opened
    panel = WebDriverWait(e2e_browser.driver, 12).until(lambda driver: driver.find_element(By.CSS_SELECTOR, f'.file-editor-panel[data-file-path="{str(target)}"]'))
    view = panel.find_element(By.CSS_SELECTOR, '[data-editor-surface="view-editor"] .ProseMirror')
    text = panel.find_element(By.CSS_SELECTOR, '[data-editor-surface="text-editor"] .cm-content')

    view.click()
    view.send_keys(Keys.HOME, Keys.DELETE, "hello")
    view.send_keys(Keys.END, Keys.ENTER, "world")
    view_state = WebDriverWait(e2e_browser.driver, 12).until(lambda driver: driver.execute_script("const p=arguments[0]; const s=fileEditorPanelState(p).content; return s.includes('hello') && s.endsWith('world') && p._cmView.state.doc.toString()===s ? {source:s, pm:p._pmView.state.doc.textContent, cm:p._cmView.state.doc.toString()} : false", panel))
    assert "<br>" not in view_state["source"], view_state
    assert view_state["source"].endswith("world"), view_state

    text.click()
    text.send_keys(Keys.END, " from-text")
    text_state = WebDriverWait(e2e_browser.driver, 12).until(lambda driver: driver.execute_script("const p=arguments[0]; const s=fileEditorPanelState(p).content; return s.endsWith('from-text') && p._pmView.state.doc.textContent.includes('from-text') ? {source:s, pm:p._pmView.state.doc.textContent, connected:p._pmView.dom.isConnected} : false", panel))
    assert text_state["connected"] is True, text_state
    e2e_browser.driver.execute_script("window.dispatchEvent(new Event('load'))")
    visible_after_refresh = WebDriverWait(e2e_browser.driver, 4).until(lambda driver: driver.execute_script("return arguments[0]._pmView?.dom?.isConnected === true && arguments[0].querySelectorAll('.ProseMirror').length === 1", panel))
    assert visible_after_refresh is True
    e2e_browser.driver.execute_script("refreshOpenEditorThemePanels(); applyEditorWrapPreference(); refreshEditorPreviews();")
    visible_after_all_refreshes = WebDriverWait(e2e_browser.driver, 4).until(lambda driver: driver.execute_script("return arguments[0]._pmView?.dom?.isConnected === true && arguments[0].querySelector('[data-editor-surface=\\\"view-editor\\\"] .ProseMirror')?.textContent.includes('from-text')", panel))
    assert visible_after_all_refreshes is True

    def open_context_and_read() -> dict[str, Any]:
        e2e_browser.driver.execute_script("const p=arguments[0]._pmView; p.dispatch(p.state.tr.setSelection(YOLOmuxProseMirror.TextSelection.create(p.state.doc, 1, 5))); p.dom.dispatchEvent(new MouseEvent('contextmenu', {bubbles:true,cancelable:true,clientX:30,clientY:30}));", panel)
        return WebDriverWait(e2e_browser.driver, 4).until(lambda driver: driver.execute_script("const m=document.querySelector('.markdown-preview-context-menu'); return m ? {bold:m.querySelector('[data-markdown-command=bold]')?.getAttribute('aria-checked'), strike:m.querySelector('[data-markdown-command=strike]')?.getAttribute('aria-checked'), underline:m.querySelector('[data-markdown-command=underline]')?.getAttribute('aria-checked')} : false"))

    first_menu = open_context_and_read()
    assert first_menu["bold"] != "true", first_menu
    e2e_browser.driver.execute_script("document.querySelector('.markdown-preview-context-menu')?.querySelector('[data-markdown-command=bold]')?.click()")
    second_menu = open_context_and_read()
    assert second_menu["bold"] == "true", second_menu
    e2e_browser.driver.execute_script("document.querySelector('.markdown-preview-context-menu')?.remove()")

    mobile_menu = e2e_browser.driver.execute_script("const p=arguments[0]._pmView; p.dispatch(p.state.tr.setSelection(YOLOmuxProseMirror.TextSelection.create(p.state.doc, 1, 5))); const e=new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:30,clientY:30}); e.yolomuxTouchLongPress=true; const prevented=!p.dom.dispatchEvent(e); return {prevented, menu:Boolean(document.querySelector('.markdown-preview-context-menu'))};", panel)
    assert mobile_menu == {"prevented": True, "menu": True}, mobile_menu
    e2e_browser.driver.execute_script("document.querySelector('.markdown-preview-context-menu')?.remove()")
    assert_browser_journey_error_free(e2e_browser.driver, server_log_boundary=e2e_browser.runtime.server_log_boundary)


def test_pre_fix_real_page_content_assertion_and_spinner_assertion_are_independent(
    e2e_browser: Any,
) -> None:
    """Pin whether ef77f3fcb can render repo children while retaining pending UI."""

    dev, repo, child = _make_finder_repo_tree(e2e_browser)
    e2e_browser.load(tabs=("files",))
    e2e_browser.expand(dev, child_path=repo)
    e2e_browser.expand(repo, child_path=child)

    content = e2e_browser.read_rendered_dom(e2e_browser.finder_row(child))
    assert content["connected"] is True and "assets" in content["text"]
    e2e_browser.assert_no_pending_indicator(repo)


def test_pending_assertion_captures_evidence_after_content_is_already_present(e2e_browser: Any) -> None:
    """Prove the missing assertion catches the exact content-plus-spinner shape."""

    dev, repo, child = _make_finder_repo_tree(e2e_browser)
    e2e_browser.load(tabs=("files",))
    e2e_browser.expand(dev, child_path=repo)
    e2e_browser.expand(repo, child_path=child)
    repo_row = e2e_browser.finder_row(repo)
    content = e2e_browser.read_rendered_dom(e2e_browser.finder_row(child))
    assert content["connected"] is True and "assets" in content["text"]

    e2e_browser.driver.execute_script("arguments[0].classList.add('loading-children');", repo_row.element())
    try:
        with pytest.raises(AssertionError, match="retained a pending indicator") as failure:
            e2e_browser.assert_no_pending_indicator(repo_row)
    finally:
        e2e_browser.driver.execute_script("arguments[0].classList.remove('loading-children');", repo_row.element())

    evidence = e2e_browser.last_evidence
    assert evidence is not None and evidence.dom.is_file()
    assert evidence.screenshot is not None and evidence.screenshot.is_file()
    assert str(evidence.dom) in str(failure.value)


def test_direct_internal_differ_fixture_path_reaches_terminal_state(e2e_browser: Any) -> None:
    """Positive control: direct payload injection bypasses the broken real-page wiring."""

    _dev, repo, _child = _make_finder_repo_tree(e2e_browser)
    payload, target = _differ_payload(e2e_browser, repo)
    e2e_browser.load()
    wait_for_browser_boot(
        e2e_browser.driver,
        globals_required={
            "applySessionFilesPayloadFromPush": "function",
            "clientEventDemandDescriptor": "function",
            "clientSessionFilesWatchRequests": "function",
            "openFileSurface": "function",
            "renderFileExplorerChangesPanels": "function",
        },
        dom_anchors=("#grid",),
        timeout=12,
    )
    metrics = e2e_browser.driver.execute_async_script(
        """
        const payload = arguments[0];
        const path = arguments[1];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            await openFileSurface(differItemId);
            const request = await window.__yolomuxTestWaitFor(
              () => clientSessionFilesWatchRequests()[0] || null,
              {timeoutMs: 4000, description: 'fixture-driven Differ watch request'},
            );
            const applied = applySessionFilesPayloadFromPush(payload, request) === true;
            renderFileExplorerChangesPanels({force: true, view: 'differ'});
            const row = await window.__yolomuxTestWaitFor(
              () => document.querySelector(`#panel-__differ__ [data-open-change-file="${CSS.escape(path)}"]`),
              {timeoutMs: 4000, description: `fixture-driven Differ row for ${path}`},
            );
            done({applied, rowConnected: row?.isConnected === true});
          } catch (error) {
            done({error: String(error?.stack || error)});
          }
        })();
        """,
        payload,
        str(target),
    )
    assert not metrics.get("error") and metrics == {"applied": True, "rowConnected": True}, metrics
    state = e2e_browser.assert_reaches_terminal_state("#panel-__differ__", bound=4)
    assert state["terminal"] is True and state["pending"] == [], state


def test_real_differ_click_reaches_content_or_typed_error_within_bound(e2e_browser: Any) -> None:
    _make_finder_repo_tree(e2e_browser)
    e2e_browser.load()
    differ = e2e_browser.open_differ()
    state = e2e_browser.assert_reaches_terminal_state(differ, bound=12)
    assert state["terminal"] is True


def test_authenticated_real_page_finder_repeats_without_pending(authenticated_e2e_browser: Any) -> None:
    """A form-authenticated browser must clear pending state on the two reported repo rows."""

    ai_config, ai_child = _make_finder_repo(
        authenticated_e2e_browser,
        "ai-config",
        "master",
        ("assets", "backend", "frontend", "run", "tools"),
    )
    ant, ant_child = _make_finder_repo(
        authenticated_e2e_browser,
        "ant",
        "main",
        ("assets", "backend", "frontend", "run", "tools"),
    )
    authentication = authenticated_e2e_browser.authentication
    assert authentication is not None
    assert authentication.username == "e2e-admin" and authentication.role == "admin"
    assert any(name.startswith("yolomux_auth_") for name in authentication.cookie_names)

    session = str(authenticated_e2e_browser.runtime.tmux.sessions[0])
    respawned = run_isolated_tmux(
        authenticated_e2e_browser.runtime.tmux,
        "respawn-pane",
        "-k",
        "-t",
        f"{session}:",
        "-c",
        str(ai_config.resolve()),
        "bash",
    )
    assert respawned.returncode == 0, respawned.stderr or respawned.stdout

    def expected_pane_cwd(_driver: object) -> subprocess.CompletedProcess[str] | bool:
        result = run_isolated_tmux(
            authenticated_e2e_browser.runtime.tmux,
            "display-message",
            "-p",
            "-t",
            f"{session}:",
            "#{pane_current_path}",
        )
        pane_path = Path(result.stdout.strip()).resolve() if result.returncode == 0 else None
        return result if pane_path == ai_config.resolve() else False

    pane_cwd = WebDriverWait(authenticated_e2e_browser.driver, 12).until(expected_pane_cwd)
    assert pane_cwd.returncode == 0 and Path(pane_cwd.stdout.strip()).resolve() == ai_config.resolve(), (
        pane_cwd.stderr or pane_cwd.stdout
    )
    WebDriverWait(authenticated_e2e_browser.driver, 12).until(
        lambda _driver: (
            authenticated_e2e_browser.runtime.app.activity_transcript_service.transcripts_payload_cache_record.worker
            is None
        )
    )
    authenticated_e2e_browser.runtime.app.set_transcripts_payload_cache(
        authenticated_e2e_browser.runtime.app.build_transcripts_payload()
    )

    authenticated_e2e_browser.load(tabs=("files", session))
    metadata = authenticated_e2e_browser.driver.execute_async_script(
        """
        const session = arguments[0];
        const expectedRoot = arguments[1];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const response = await fetch('/api/session-metadata?force=1', {cache: 'no-store'});
            const raw = await response.json();
            const rendered = await window.__yolomuxTestWaitFor(() => {
              const info = transcriptMetadataState.payload.sessions?.[session];
              const summary = sessionWorkSummary(session, info);
              const tab = document.querySelector(`[data-pane-tab="${CSS.escape(session)}"]`);
              const popover = typeof paneTabPopoverForAnchor === 'function'
                ? paneTabPopoverForAnchor(tab)
                : tab?.querySelector?.(':scope > .session-popover');
              const popoverText = String(popover?.textContent || '');
              if (summary.git?.root !== expectedRoot || summary.git?.branch !== 'master') return null;
              if (!tab || !popover || !popoverText.includes('master') || !popoverText.includes(expectedRoot)) return null;
              return {
                grid: document.querySelector('#grid') !== null,
                tabText: String(tab.textContent || ''),
                tabAriaLabel: String(tab.getAttribute('aria-label') || ''),
                popoverText,
                graphRoot: summary.git.root,
                graphBranch: summary.git.branch,
              };
            }, {timeoutMs: 12000, description: 'authenticated metadata-driven tab and popover'});
            done({
              status: response.status,
              state: raw?.state || '',
              requestId: raw?.request?.id || '',
              hasCanonicalSession: Boolean(raw?.data?.sessions?.[session]),
              hasFlattenedSessions: Object.prototype.hasOwnProperty.call(raw || {}, 'sessions'),
              rendered,
            });
          } catch (error) {
            done({error: String(error?.stack || error)});
          }
        })();
            """,
            session,
            str(ai_config.resolve()),
        )
    assert not metadata.get("error"), metadata
    assert metadata["status"] == 200 and metadata["state"] == "ready" and metadata["requestId"], metadata
    assert metadata["hasCanonicalSession"] is True and metadata["hasFlattenedSessions"] is False, metadata
    assert metadata["rendered"]["grid"] is True, metadata
    assert metadata["rendered"]["graphRoot"] == str(ai_config.resolve()) and metadata["rendered"]["graphBranch"] == "master", metadata
    assert "master" in metadata["rendered"]["tabAriaLabel"] and str(ai_config.resolve()) in metadata["rendered"]["popoverText"], metadata

    authenticated_e2e_browser.expand(ai_config.parent, child_path=ai_config)
    for repo, child, branch in ((ai_config, ai_child, "master"), (ant, ant_child, "main")):
        label = authenticated_e2e_browser.driver.execute_async_script(
            """
            const row = arguments[0];
            const branch = arguments[1];
            const done = arguments[arguments.length - 1];
            window.__yolomuxTestWaitFor(
              () => String(row.innerText || '').includes(`[${branch}]`) ? String(row.innerText || '') : null,
              {timeoutMs: 12000, description: `authenticated Finder branch badge ${branch}`},
            ).then(done, error => done({error: String(error?.stack || error)}));
            """,
            authenticated_e2e_browser.finder_row(repo).element(),
            branch,
        )
        assert not isinstance(label, dict) and f"[{branch}]" in label, label

        def exercise_repo() -> dict[str, object]:
            authenticated_e2e_browser.re_expand(repo, child_path=child)
            pending = authenticated_e2e_browser.assert_no_pending_indicator(repo)
            child_dom = authenticated_e2e_browser.read_rendered_dom(authenticated_e2e_browser.finder_row(child))
            return {"pending": pending, "child": child_dom}

        repeated = authenticated_e2e_browser.assert_repeated(exercise_repo, times=5)
        assert len(repeated) == 5
        assert all(item["child"]["connected"] is True for item in repeated)

    authenticated_e2e_browser.driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
        authenticated_e2e_browser.finder_row(ant).element(),
    )
    evidence = authenticated_e2e_browser.capture_failure("authenticated-finder-pass")
    assert evidence.screenshot is not None and evidence.screenshot.is_file()
    assert evidence.dom.is_file()


def test_authenticated_release_soak_gates_browser_and_server_failures_without_stats_panel(
    authenticated_e2e_browser: Any,
) -> None:
    """The release soak fails from retained browser and server evidence without opening YO!stats."""

    authentication = authenticated_e2e_browser.authentication
    assert authentication is not None and authentication.role == "admin"
    driver = authenticated_e2e_browser.driver
    begin_browser_journey_surface_tracking(driver)
    driver.get_log("browser")
    baseline = driver.execute_script(
        """
        window.__releaseGateLogsRequests = 0;
        window.__releaseGateOriginalFetch = window.fetch;
        window.fetch = (input, options = {}) => {
          const path = new URL(String(input), location.href).pathname;
          if (path === '/api/logs') window.__releaseGateLogsRequests += 1;
          return window.__releaseGateOriginalFetch(input, options);
        };
        return {
          statsPanelPresent: document.querySelector('.js-debug-panel') !== null,
          logsRequests: window.__releaseGateLogsRequests,
        };
        """
    )
    assert baseline == {"statsPanelPresent": False, "logsRequests": 0}

    acknowledge_browser_diagnostic_receipts(driver)
    clean = assert_browser_journey_error_free(driver)
    assert clean["browserLocalFailures"] == []
    assert clean["serverLogErrors"] == []
    clean_ring_reads = driver.execute_script("return window.__releaseGateLogsRequests;")
    assert clean_ring_reads > 0

    stats_failure_event = emit_js_debug_event(
        driver,
        "stats_history",
        {
            "level": "warning",
            "message": "production graph refresh failed",
            "wallTime": "2026-08-05 13:23:45 PDT",
            "requestId": "r-production-graph-17",
            "source": "stats-current",
            "endpoint": "/api/stats-snapshot",
            "eventType": "graph-refresh",
            "deliveryOutcome": "failed",
        },
    )
    with pytest.raises(AssertionError, match="production graph refresh failed") as failure:
        assert_browser_journey_error_free(driver)
    evidence = str(failure.value)
    evidence_payload = json.loads(evidence.removeprefix("browser journey emitted errors: "))
    for retained in (
        '"requestId": "r-production-graph-17"',
        '"source": "stats-current"',
        '"route": "/api/stats-snapshot"',
        '"event": "graph-refresh"',
        '"deliveryOutcome": "failed"',
    ):
        assert retained in evidence, evidence
    assert evidence_payload["browserLocalFailures"][0]["wallTime"] == "2026-08-05 13:23:45 PDT"
    assert evidence_payload["browserLocalFailures"][0]["wallTime"] != stats_failure_event["ts"]
    assert driver.execute_script("return window.__releaseGateLogsRequests;") > clean_ring_reads
    assert driver.execute_script("return document.querySelector('.js-debug-panel') === null;") is True
    assert acknowledge_and_consume_only_expected_js_debug_failures(driver, (stats_failure_event,)) == (
        stats_failure_event,
    )

    client_failure_event = emit_js_debug_event(
        driver,
        "client_failure",
        {
            "level": "error",
            "message": "authenticated activity graph refresh failed",
            "wallTime": "2026-08-05 13:24:00 PDT",
            "requestId": "r-authenticated-activity-graph-18",
            "source": "activity-graph",
            "endpoint": "/api/activity-summary",
            "eventType": "graph-refresh",
            "deliveryOutcome": "failed",
        },
    )
    with pytest.raises(AssertionError, match="authenticated activity graph refresh failed") as error_failure:
        assert_browser_journey_error_free(driver)
    error_evidence = json.loads(
        str(error_failure.value).removeprefix("browser journey emitted errors: ")
    )
    assert len(error_evidence["browserLocalFailures"]) == 1, error_evidence
    error_record = error_evidence["browserLocalFailures"][0]
    assert {
        "level": error_record["level"],
        "message": error_record["message"],
        "requestId": error_record["requestId"],
        "source": error_record["source"],
        "route": error_record["route"],
        "event": error_record["event"],
        "wallTime": error_record["wallTime"],
        "deliveryOutcome": error_record["deliveryOutcome"],
    } == {
        "level": "error",
        "message": "authenticated activity graph refresh failed",
        "requestId": "r-authenticated-activity-graph-18",
        "source": "activity-graph",
        "route": "/api/activity-summary",
        "event": "graph-refresh",
        "wallTime": "2026-08-05 13:24:00 PDT",
        "deliveryOutcome": "failed",
    }
    assert error_record["wallTime"] != client_failure_event["ts"]
    assert driver.execute_script("return document.querySelector('.js-debug-panel') === null;") is True
    assert acknowledge_and_consume_only_expected_js_debug_failures(driver, (client_failure_event,)) == (
        client_failure_event,
    )
    server_warning = {
        "level": "warning",
        "source": "local-service:statusd",
        "category": "transport",
        "message": "authenticated server-only transport warning",
    }
    SERVER_LOGS.emit(
        server_warning["level"],
        server_warning["source"],
        server_warning["message"],
        category=server_warning["category"],
    )
    with pytest.raises(AssertionError, match=server_warning["message"]) as server_failure:
        assert_browser_journey_error_free(driver)
    server_evidence = str(server_failure.value)
    assert '"source": "local-service:statusd"' in server_evidence
    assert '"level": "warning"' in server_evidence
    assert retire_expected_fixture_server_log_errors(
        driver,
        authenticated_e2e_browser.runtime,
        (server_warning,),
    )[0]["message"] == server_warning["message"]
    assert driver.execute_script("return document.querySelector('.js-debug-panel') === null;") is True

    clean_after_failure = assert_browser_journey_error_free(driver)
    assert clean_after_failure["browserLocalFailures"] == [] and clean_after_failure["serverLogErrors"] == []
    driver.execute_script(
        "window.fetch = window.__releaseGateOriginalFetch; delete window.__releaseGateOriginalFetch;"
    )
