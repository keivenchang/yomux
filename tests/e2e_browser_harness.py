# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Reusable real-server, real-Chrome end-to-end browser test support.

A test that loads a static fixture page is not end-to-end. It cannot catch a
defect in how the real product page wires server data, browser state, and the
rendered DOM together, and three user-reported defects have now escaped that
way. This harness composes the shared gate server resources, then drives the
actual page with Chrome and preserves screenshot/DOM evidence.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Any, TypeVar
from urllib.parse import urlencode
from urllib.parse import urlparse
import uuid

import pytest

from tests.browser_helpers.browser_layout import assert_live_runtime_boot_healthy
from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_layout import install_live_runtime_boot_error_tracker
from tests.browser_helpers.browser_layout import register_browser_new_document_script
from tests.browser_helpers.browser_layout import WebDriverWait
from tests.gate_harness import GateAuthCredentials
from tests.gate_harness import GateLiveServer
from tests.gate_harness import gate_auth_credentials  # noqa: F401
from tests.gate_harness import gate_authenticated_live_server  # noqa: F401
from tests.gate_harness import gate_http_port  # noqa: F401
from tests.gate_harness import gate_live_server  # noqa: F401
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import gate_tmux  # noqa: F401
from tests.gate_harness import repeat
from tests.gate_harness import wait_for_browser_boot
from tests.gate_harness import wait_for_fixture_client_event_demand


T = TypeVar("T")
E2E_EVIDENCE_ROOT_ENV = "YOLOMUX_E2E_EVIDENCE_DIR"
DEFAULT_E2E_EVIDENCE_ROOT = Path(os.environ.get("YOLOMUX_TEST_ROOT", "/tmp/yolomux-test-e2e")) / "evidence"
DEFAULT_BROWSER_BOUND_SECONDS = 20.0
FINDER_PANEL_SELECTOR = "#panel-__finder__"
DIFFER_PANEL_SELECTOR = "#panel-__differ__"
PENDING_SELECTORS = (
    ".loading-children",
    ".loading",
    ".info-loading-spinner",
    "[aria-busy='true']",
)
TYPED_ERROR_SELECTORS = (
    ".file-tree-status-error",
    ".file-editor-status.error",
    "[data-error-code]",
    "[data-reason]",
    "[role='alert']",
)

API_JOURNEY_OBSERVER_SOURCE = r"""
  (() => {
    if (window.__yolomuxApiJourneyObserver?.installed === true) return;
    const state = {
      installed: true,
      records: [],
      pending: [],
      sequence: 0,
    };
    const apiTarget = rawUrl => {
      try {
        const url = new URL(String(rawUrl || ''), location.href);
        return url.pathname.startsWith('/api/') ? url : null;
      } catch (_error) {
        return null;
      }
    };
    const begin = (transport, method, url) => {
      const record = {
        sequence: ++state.sequence,
        transport,
        method: String(method || 'GET').toUpperCase(),
        path: url.pathname,
        search: url.search,
        url: url.href,
        status: null,
        contentType: '',
        body: '',
        error: '',
        startedAtMs: performance.now(),
        settledAtMs: null,
      };
      state.records.push(record);
      return record;
    };
    const track = promise => {
      const tracked = Promise.resolve(promise).finally(() => {
        const index = state.pending.indexOf(tracked);
        if (index >= 0) state.pending.splice(index, 1);
      });
      state.pending.push(tracked);
      return tracked;
    };
    const captureFailureBody = (response, record) => {
      if (response.status >= 200 && response.status < 300) return;
      track(response.clone().text().then(body => {
        record.body = String(body || '').slice(0, 16 * 1024);
      }).catch(error => {
        record.error = `response body read failed: ${String(error?.message || error)}`;
      }));
    };

    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, options = {}) => {
      const target = apiTarget(typeof input === 'string' || input instanceof URL ? input : input?.url);
      if (!target) return originalFetch(input, options);
      const method = options?.method || input?.method || 'GET';
      const record = begin('fetch', method, target);
      return track((async () => {
        try {
          const response = await originalFetch(input, options);
          record.status = Number(response.status || 0);
          record.contentType = String(response.headers.get('content-type') || '');
          record.settledAtMs = performance.now();
          captureFailureBody(response, record);
          return response;
        } catch (error) {
          record.status = 0;
          record.error = String(error?.stack || error);
          record.settledAtMs = performance.now();
          throw error;
        }
      })());
    };

    const xhrOpen = XMLHttpRequest.prototype.open;
    const xhrSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
      this.__yolomuxApiJourney = {method, target: apiTarget(url)};
      return xhrOpen.call(this, method, url, ...rest);
    };
    XMLHttpRequest.prototype.send = function(...args) {
      const request = this.__yolomuxApiJourney;
      if (request?.target) {
        const record = begin('xhr', request.method, request.target);
        let settle;
        track(new Promise(resolve => { settle = resolve; }));
        this.addEventListener('loadend', () => {
          record.status = Number(this.status || 0);
          record.contentType = String(this.getResponseHeader('content-type') || '');
          record.settledAtMs = performance.now();
          if (record.status < 200 || record.status >= 300) {
            try { record.body = String(this.responseText || '').slice(0, 16 * 1024); }
            catch (error) { record.error = `response body read failed: ${String(error?.message || error)}`; }
          }
          settle();
        }, {once: true});
      }
      return xhrSend.apply(this, args);
    };

    const NativeEventSource = window.EventSource;
    if (typeof NativeEventSource === 'function') {
      function ObservedEventSource(url, options) {
        const source = new NativeEventSource(url, options);
        const target = apiTarget(url);
        if (target) {
          const record = begin('eventsource', 'GET', target);
          record.opened = false;
          record.closedByClient = false;
          const nativeClose = source.close.bind(source);
          source.close = () => {
            record.closedByClient = true;
            if (record.status === null) {
              record.status = 200;
              record.contentType = 'text/event-stream';
              record.settledAtMs = performance.now();
            }
            return nativeClose();
          };
          source.addEventListener('open', () => {
            record.opened = true;
            record.status = 200;
            record.contentType = 'text/event-stream';
            record.settledAtMs = performance.now();
          }, {once: true});
          source.addEventListener('error', () => {
            if (record.opened) return;
            record.status = 0;
            record.error = 'EventSource failed before opening';
            record.settledAtMs = performance.now();
          });
        }
        return source;
      }
      ObservedEventSource.prototype = NativeEventSource.prototype;
      for (const name of ['CONNECTING', 'OPEN', 'CLOSED']) {
        Object.defineProperty(ObservedEventSource, name, {value: NativeEventSource[name]});
      }
      window.EventSource = ObservedEventSource;
    }
    window.__yolomuxApiJourneyObserver = state;
  })();
"""


@dataclass(frozen=True)
class BrowserEvidence:
    screenshot: Path | None
    dom: Path
    capture_errors: tuple[str, ...] = ()

    def message(self) -> str:
        screenshot = str(self.screenshot) if self.screenshot is not None else "unavailable"
        suffix = f" capture_errors={list(self.capture_errors)!r}" if self.capture_errors else ""
        return f"E2E browser evidence: screenshot={screenshot} dom={self.dom}{suffix}"


@dataclass(frozen=True)
class FinderRow:
    """Stable Finder row identity that re-queries after product DOM replacement."""

    harness: "E2EBrowserHarness"
    path: Path

    def element(self):
        return self.harness.finder_row_element(self.path)


@dataclass(frozen=True)
class BrowserAuthentication:
    """Observable proof that Chrome completed the product's real login form."""

    username: str
    role: str
    login_url: str
    app_url: str
    cookie_names: tuple[str, ...]


class E2EBrowserHarness:
    """Bounded user actions and rendered-state assertions for the actual app."""

    def __init__(
        self,
        driver,
        runtime: GateLiveServer,
        *,
        test_id: str,
        evidence_root: Path,
        bound: float = DEFAULT_BROWSER_BOUND_SECONDS,
    ) -> None:
        if bound <= 0:
            raise ValueError("browser operation bound must be positive")
        self.driver = driver
        self.runtime = runtime
        self.test_id = test_id
        self.evidence_root = evidence_root
        self.bound = float(bound)
        self._last_evidence: BrowserEvidence | None = None
        self._authentication: BrowserAuthentication | None = None
        self.driver.set_page_load_timeout(self.bound)
        self.driver.set_script_timeout(self.bound)

    @property
    def base_url(self) -> str:
        return self.runtime.base_url

    @property
    def last_evidence(self) -> BrowserEvidence | None:
        return self._last_evidence

    @property
    def authentication(self) -> BrowserAuthentication | None:
        return self._authentication

    def _wait_for_real_app(self, url: str) -> None:
        assert_live_runtime_boot_healthy(self.driver, self.test_id, timeout=min(self.bound, 12))
        wait_for_browser_boot(
            self.driver,
            globals_required={
                "openFileExplorerAt": "function",
                "expandDirectoryRow": "function",
            },
            dom_anchors=("#grid", FINDER_PANEL_SELECTOR),
            timeout=min(self.bound, 12),
        )
        demand = wait_for_fixture_client_event_demand(self.driver, timeout=min(self.bound, 12))
        parsed_url = urlparse(url)
        assert demand["sourceOrigin"] == f"{parsed_url.scheme}://{parsed_url.netloc}", demand
        if self._authentication is None:
            return
        identity = self.driver.execute_script(
            "return {username: String(authUsername || ''), role: String(accessRole || '')};"
        )
        expected = {"username": self._authentication.username, "role": self._authentication.role}
        if identity != expected:
            self._raise_with_evidence(
                f"authenticated app identity changed at {url}: expected={expected}, actual={identity}",
                label="authentication-identity",
            )

    def install_api_journey_observer(self) -> str:
        """Install the derived API observer before the next real page navigation."""

        return register_browser_new_document_script(self.driver, API_JOURNEY_OBSERVER_SOURCE)

    def reset_api_journey_observations(self) -> None:
        """Clear observations without replacing any product transport."""

        result = self.driver.execute_script(
            """
            const state = window.__yolomuxApiJourneyObserver;
            if (!state?.installed) return false;
            state.records.length = 0;
            state.sequence = 0;
            return true;
            """
        )
        if result is not True:
            self._raise_with_evidence("API journey observer is not installed", label="api-observer-reset")

    def api_journey_observations(self) -> list[dict[str, Any]]:
        """Wait for bounded failure bodies and return every observed API response."""

        result = self.driver.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            const state = window.__yolomuxApiJourneyObserver;
            if (!state?.installed) { done({error: 'API journey observer is not installed'}); return; }
            (async () => {
              for (let pass = 0; pass < 8; pass += 1) {
                const pending = Array.from(state.pending);
                if (!pending.length) break;
                await Promise.allSettled(pending);
              }
              done({records: structuredClone(state.records), pending: state.pending.length});
            })().catch(error => done({error: String(error?.stack || error)}));
            """
        )
        if result.get("error") or result.get("pending"):
            self._raise_with_evidence(f"API journey observations did not settle: {result}", label="api-observer-read")
        return [dict(record) for record in result.get("records") or ()]

    def load(self, *, tabs: tuple[str, ...] | None = None) -> str:
        """Load the real product page with Finder, Differ, and one real session."""

        session = str(self.runtime.tmux.sessions[0])
        requested_tabs = tabs or ("files", "diff", session)
        query = urlencode({
            "sessions": session,
            "layout": "left",
            "tabs": f"left:{','.join(requested_tabs)}",
        })
        install_live_runtime_boot_error_tracker(self.driver)
        url = f"{self.base_url}/?{query}"
        self.driver.get(url)
        self._wait_for_real_app(url)
        return url

    def authenticate(self, credentials: GateAuthCredentials) -> BrowserAuthentication:
        """Establish a cookie session by submitting the same login form as a user."""

        install_live_runtime_boot_error_tracker(self.driver)
        self.driver.get(f"{self.base_url}/")
        login_url = self.driver.current_url
        if urlparse(login_url).path != "/login":
            self._raise_with_evidence(
                f"unauthenticated app request did not redirect to /login: {login_url}",
                label="authentication-redirect",
            )
        form = self.driver.find_element("css selector", 'form[action="/login"]')
        username = form.find_element("css selector", 'input[name="username"]')
        password = form.find_element("css selector", 'input[name="password"]')
        username.send_keys(credentials.username)
        password.send_keys(credentials.password)
        form.find_element("css selector", 'button[type="submit"]').click()
        app_url = WebDriverWait(self.driver, min(self.bound, 12)).until(
            lambda driver: driver.current_url if urlparse(driver.current_url).path != "/login" else False
        )
        self._authentication = BrowserAuthentication(
            username=credentials.username,
            role=credentials.role,
            login_url=login_url,
            app_url=app_url,
            cookie_names=tuple(sorted(str(cookie.get("name") or "") for cookie in self.driver.get_cookies())),
        )
        self._wait_for_real_app(app_url)
        return self._authentication

    @staticmethod
    def _path_text(path: str | Path) -> str:
        return str(path)

    def finder_row_element(self, path: str | Path, *, bound: float | None = None):
        """Wait for and return the currently connected Finder row for ``path``."""

        timeout_ms = round(float(bound or self.bound) * 1000)
        result = self.driver.execute_async_script(
            """
            const path = arguments[0];
            const timeoutMs = arguments[1];
            const done = arguments[arguments.length - 1];
            const selector = `#panel-__finder__ .file-tree-row[data-path="${CSS.escape(path)}"]`;
            window.__yolomuxTestWaitFor(
              () => document.querySelector(selector),
              {timeoutMs, description: `connected Finder row for ${path}`},
            ).then(done, error => done({__e2eError: String(error?.stack || error)}));
            """,
            self._path_text(path),
            timeout_ms,
        )
        if isinstance(result, dict) and result.get("__e2eError"):
            self._raise_with_evidence(str(result["__e2eError"]), label="finder-row")
        return result

    def finder_row(self, path: str | Path, *, bound: float | None = None) -> FinderRow:
        normalized = Path(self._path_text(path))
        self.finder_row_element(normalized, bound=bound)
        return FinderRow(self, normalized)

    def click_finder_row(self, row: FinderRow | str | Path) -> FinderRow:
        target = row if isinstance(row, FinderRow) else self.finder_row(row)
        target.element().click()
        return target

    def expand(
        self,
        row: FinderRow | str | Path,
        *,
        child_path: str | Path | None = None,
        bound: float | None = None,
    ) -> FinderRow:
        """Expand through the row's real click handler and wait for rendered children."""

        target = row if isinstance(row, FinderRow) else self.finder_row(row)
        element = target.element()
        if element.get_attribute("aria-expanded") != "true":
            element.click()
        timeout_ms = round(float(bound or self.bound) * 1000)
        result = self.driver.execute_async_script(
            """
            const path = arguments[0];
            const childPath = arguments[1];
            const timeoutMs = arguments[2];
            const done = arguments[arguments.length - 1];
            const rowFor = () => document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${CSS.escape(path)}"]`);
            const childFor = () => childPath
              ? document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${CSS.escape(childPath)}"]`)
              : rowFor()?.nextElementSibling?.querySelector?.('.file-tree-row[data-path]');
            window.__yolomuxTestWaitFor(
              () => rowFor()?.getAttribute('aria-expanded') === 'true' && childFor(),
              {timeoutMs, description: `rendered Finder children for ${path}`},
            ).then(
              child => done({path: child?.dataset?.path || '', contentPresent: true}),
              error => done({__e2eError: String(error?.stack || error)}),
            );
            """,
            self._path_text(target.path),
            self._path_text(child_path) if child_path is not None else "",
            timeout_ms,
        )
        if result.get("__e2eError"):
            self._raise_with_evidence(str(result["__e2eError"]), label="finder-expand")
        if not result.get("contentPresent"):
            self._raise_with_evidence(f"Finder children did not render for {target.path}", label="finder-expand")
        return target

    def collapse(self, row: FinderRow | str | Path, *, bound: float | None = None) -> FinderRow:
        """Collapse through the current row's real click handler."""

        target = row if isinstance(row, FinderRow) else self.finder_row(row)
        element = target.element()
        if element.get_attribute("aria-expanded") == "true":
            element.click()
        self._wait_for_row_state(target.path, expanded=False, bound=bound)
        return target

    def re_expand(
        self,
        row: FinderRow | str | Path,
        *,
        child_path: str | Path | None = None,
        bound: float | None = None,
    ) -> FinderRow:
        target = self.collapse(row, bound=bound)
        return self.expand(target, child_path=child_path, bound=bound)

    def _wait_for_row_state(self, path: str | Path, *, expanded: bool, bound: float | None = None) -> dict[str, Any]:
        timeout_ms = round(float(bound or self.bound) * 1000)
        result = self.driver.execute_async_script(
            """
            const path = arguments[0];
            const expanded = arguments[1];
            const timeoutMs = arguments[2];
            const done = arguments[arguments.length - 1];
            const rowFor = () => document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${CSS.escape(path)}"]`);
            window.__yolomuxTestWaitFor(() => {
              const row = rowFor();
              return row && row.getAttribute('aria-expanded') === String(expanded) && !row.classList.contains('loading-children');
            }, {timeoutMs, description: `Finder ${expanded ? 'expanded' : 'collapsed'} state for ${path}`}).then(
              () => {
                const row = rowFor();
                done({expanded: row?.getAttribute('aria-expanded') || '', className: row?.className || ''});
              },
              error => done({__e2eError: String(error?.stack || error)}),
            );
            """,
            self._path_text(path),
            expanded,
            timeout_ms,
        )
        if result.get("__e2eError"):
            self._raise_with_evidence(str(result["__e2eError"]), label="finder-row-state")
        return result

    def open_differ(self, *, bound: float | None = None):
        """Click the rendered Differ pane tab and return its visible panel."""

        selector = '.dockview-pane-tab[data-pane-tab="__differ__"]'
        tab = self._visible_element(selector, bound=bound)
        tab.click()
        return self._visible_element(DIFFER_PANEL_SELECTOR, bound=bound)

    def switch_session(self, session: str, *, bound: float | None = None):
        """Click a rendered session tab and wait for its panel to become visible."""

        tab = self._visible_element(f'.dockview-pane-tab[data-pane-tab="{session}"]', bound=bound)
        tab.click()
        return self._visible_element(f"#panel-{session}", bound=bound)

    def _visible_element(self, selector: str, *, bound: float | None = None):
        timeout_ms = round(float(bound or self.bound) * 1000)
        result = self.driver.execute_async_script(
            """
            const selector = arguments[0];
            const timeoutMs = arguments[1];
            const done = arguments[arguments.length - 1];
            window.__yolomuxTestWaitFor(() => {
              const node = document.querySelector(selector);
              if (!node) return null;
              const rect = node.getBoundingClientRect();
              const style = getComputedStyle(node);
              return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' ? node : null;
            }, {timeoutMs, description: `visible ${selector}`}).then(done, error => done({__e2eError: String(error?.stack || error)}));
            """,
            selector,
            timeout_ms,
        )
        if isinstance(result, dict) and result.get("__e2eError"):
            self._raise_with_evidence(str(result["__e2eError"]), label="visible-element")
        return result

    def read_rendered_dom(self, target: FinderRow | str) -> dict[str, Any]:
        """Read connected rendered DOM, text, classes, and accessibility state."""

        if isinstance(target, FinderRow):
            node = target.element()
        else:
            node = self.driver.find_element("css selector", target)
        return self.driver.execute_script(
            """
            const node = arguments[0];
            return {
              connected: node?.isConnected === true,
              html: node?.outerHTML || '',
              text: node?.innerText || '',
              className: node?.className || '',
              ariaBusy: node?.getAttribute?.('aria-busy') || '',
              ariaExpanded: node?.getAttribute?.('aria-expanded') || '',
            };
            """,
            node,
        )

    def assert_no_pending_indicator(self, row: FinderRow | str | Path) -> dict[str, Any]:
        """Assert the row has neither the rendered spinner nor pending state."""

        target = row if isinstance(row, FinderRow) else self.finder_row(row)
        state = self.driver.execute_script(
            """
            const path = arguments[0];
            const row = document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${CSS.escape(path)}"]`);
            return {
              path,
              connected: row?.isConnected === true,
              loadingClass: row?.classList.contains('loading-children') === true,
              pendingSet: fileExplorerPendingExpansions.has(path),
              className: row?.className || '',
              html: row?.outerHTML || '',
            };
            """,
            self._path_text(target.path),
        )
        if not state["connected"] or state["loadingClass"] or state["pendingSet"]:
            self._raise_with_evidence(f"Finder row retained a pending indicator: {state}", label="pending-indicator")
        return state

    def assert_reaches_terminal_state(self, panel, bound: float) -> dict[str, Any]:
        """Require content or a typed visible error with no indefinite loader."""

        timeout_ms = round(float(bound) * 1000)
        if timeout_ms <= 0:
            raise ValueError("terminal-state bound must be positive")
        result = self.driver.execute_async_script(
            """
            const target = arguments[0];
            const pendingSelectors = arguments[1];
            const errorSelectors = arguments[2];
            const timeoutMs = arguments[3];
            const done = arguments[arguments.length - 1];
            const resolvePanel = () => typeof target === 'string' ? document.querySelector(target) : target;
            const inspect = () => {
              const panel = resolvePanel();
              if (!panel) return null;
              const visible = node => {
                if (!node) return false;
                const rect = node.getBoundingClientRect();
                const style = getComputedStyle(node);
                return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
              };
              const matching = selector => [panel, ...panel.querySelectorAll(selector)].filter(node => node.matches(selector));
              const pending = pendingSelectors.filter(selector => matching(selector).some(visible));
              const typedError = errorSelectors.find(selector => matching(selector).some(visible)) || '';
              const text = String(panel.innerText || '').trim();
              const contentRows = panel.querySelectorAll('.file-tree-row[data-path], .cm-content, .xterm-screen').length;
              return {pending, typedError, text, contentRows, terminal: pending.length === 0 && (Boolean(typedError) || contentRows > 0 || text.length > 0)};
            };
            window.__yolomuxTestWaitFor(() => inspect()?.terminal ? inspect() : null, {
              timeoutMs,
              description: 'panel content or typed error without a pending indicator',
            }).then(done, error => done({__e2eError: String(error?.stack || error), last: inspect()}));
            """,
            panel,
            list(PENDING_SELECTORS),
            list(TYPED_ERROR_SELECTORS),
            timeout_ms,
        )
        if result.get("__e2eError"):
            self._raise_with_evidence(f"{result['__e2eError']}; last={result.get('last')}", label="terminal-state")
        return result

    def assert_repeated(self, action: Callable[[], T], times: int = 5) -> list[T]:
        """Run the same user action consecutively with iteration-aware failure."""

        try:
            return repeat(times, lambda _iteration: action())
        except Exception as error:
            self._raise_with_evidence(str(error), label="repeated-action", cause=error)

    def capture_failure(self, label: str = "failure") -> BrowserEvidence:
        """Persist a screenshot and full DOM under the mounted /tmp evidence root."""

        safe_test_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", self.test_id).strip("-") or "e2e"
        safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-") or "failure"
        directory = self.evidence_root / f"{safe_test_id}-{safe_label}-{uuid.uuid4().hex[:8]}"
        directory.mkdir(parents=True, exist_ok=False)
        screenshot = directory / "screenshot.png"
        dom = directory / "dom.html"
        errors = []
        screenshot_path: Path | None = screenshot
        try:
            if not self.driver.save_screenshot(str(screenshot)):
                errors.append("Chrome returned false while saving the screenshot")
                screenshot_path = None
        except Exception as error:
            errors.append(f"screenshot: {error}")
            screenshot_path = None
        try:
            dom.write_text(self.driver.page_source, encoding="utf-8")
        except Exception as error:
            errors.append(f"DOM: {error}")
            dom.write_text(f"DOM capture failed: {error}\n", encoding="utf-8")
        evidence = BrowserEvidence(screenshot=screenshot_path, dom=dom, capture_errors=tuple(errors))
        self._last_evidence = evidence
        return evidence

    def _raise_with_evidence(self, message: str, *, label: str, cause: Exception | None = None) -> None:
        evidence = self.capture_failure(label)
        error = AssertionError(f"{message}\n{evidence.message()}")
        if cause is None:
            raise error
        raise error from cause


@contextmanager
def _e2e_browser_context(request, browser, runtime: GateLiveServer):
    assert 7900 <= runtime.port <= 7999, runtime.port
    evidence_root = Path(os.environ.get(E2E_EVIDENCE_ROOT_ENV, str(DEFAULT_E2E_EVIDENCE_ROOT)))
    harness = E2EBrowserHarness(
        browser,
        runtime,
        test_id=request.node.nodeid,
        evidence_root=evidence_root,
    )
    # The owning real-server fixture gates and blanks this browser while its origin is live.
    yield harness


@pytest.fixture
def e2e_browser(request, browser, gate_live_server: GateLiveServer) -> E2EBrowserHarness:
    """Provide the real app in Chrome on isolated 7900s resources."""

    with _e2e_browser_context(request, browser, gate_live_server) as harness:
        yield harness


@pytest.fixture
def authenticated_e2e_browser(
    request,
    browser,
    gate_authenticated_live_server: GateLiveServer,
    gate_auth_credentials: GateAuthCredentials,
) -> E2EBrowserHarness:
    """Provide a real Chrome session established through the product login form."""

    with _e2e_browser_context(request, browser, gate_authenticated_live_server) as harness:
        harness.install_api_journey_observer()
        harness.authenticate(gate_auth_credentials)
        yield harness


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Attach diagnosable browser evidence to any failed harness test."""

    outcome = yield
    report = outcome.get_result()
    if call.when != "call" or not report.failed:
        return
    harness = next((value for value in item.funcargs.values() if isinstance(value, E2EBrowserHarness)), None)
    if not isinstance(harness, E2EBrowserHarness):
        return
    evidence = harness.capture_failure("pytest-call")
    report.sections.append(("E2E browser evidence", evidence.message()))
