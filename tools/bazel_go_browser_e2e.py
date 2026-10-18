#!/usr/bin/env python3
"""Real Chromium smoke for the Go terminal page and PTY resize contract."""

import argparse
import atexit
import fcntl
import hashlib
import http.cookiejar
import json
import os
import pty
import signal
import shutil
from pathlib import Path
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import termios
import time
import urllib.error
import urllib.request

from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.support.ui import WebDriverWait

# Direct script invocations start with ``tools/`` as sys.path[0]. Add the runfiles/source root so
# the same import topology works for local diagnostics and for the Bazel wrapper.
_SCRIPT_SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_SOURCE_ROOT))

from tests.browser_helpers.webdriver_lease import WebDriverLease
from tools.product_environment import product_env, product_env_name, require_product_env
from tools.instance_isolation import synthetic_test_environment as isolated_test_environment
from tools.instance_isolation import normalize_yomux_environment
from tools.tmux_paths import short_tmux_socket_path
from tools.tabber_browser_journey import isolated_attention_journey, normal_multiwindow_journey, normal_tabber_journey


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def synthetic_server_environment(overrides: dict[str, str]) -> dict[str, str]:
    """Remove ambient product/client roots before launching the fixture server."""
    return isolated_test_environment(overrides)


def resolve_source_root() -> Path:
    configured = product_env('E2E_SOURCE_ROOT', "", environ=os.environ).strip()
    source_root = Path(configured) if configured else Path.cwd()
    required = (source_root / "static/yomux.js", source_root / "tools", source_root / "yomux_lib", source_root / "assets/runtime")
    if not all(path.exists() for path in required):
        missing = ", ".join(str(path) for path in required if not path.exists())
        raise RuntimeError(f"E2E source root is missing required assets: {missing}")
    return source_root.resolve()


def tabber_reload_trace_source(session: str) -> str:
    return """
        (() => {
          const targetSession = __TARGET_SESSION__;
          const trace = {requests: [], activity: null, firstRowAt: null, firstRowText: '', metadataConvergence: null};
          window.__tabberReloadTrace = trace;
          const sourceRequest = () => trace.requests
            .filter(request => request.responseAt !== null && request.responseAt <= trace.firstRowAt
              && request.body?.sessions?.[targetSession])
            .sort((left, right) => left.responseAt - right.responseAt)[0];
          const observeConvergence = () => {
            if (trace.metadataConvergence || trace.firstRowAt === null) return;
            const request = sourceRequest();
            if (!request) return;
            const body = request.body;
            const baseline = Number(body.metadata_generation || body.metadata_identity?.generation || body.cache?.generation || 0);
            if (body.cache?.stale !== true && body.cache?.refreshing !== true) {
              trace.metadataConvergence = {generation: baseline, baseline, at: request.responseAt, skipped: 'cache is current'};
              return;
            }
            const state = typeof transcriptMetadataState === 'object' ? transcriptMetadataState : null;
            const generation = Number(state?.generation || 0);
            const sessionData = state?.payload?.sessions?.[targetSession] || null;
            const at = performance.now();
            if (generation > baseline && sessionData?.work_graph?.version === 1) {
              trace.metadataConvergence = {generation, baseline, at, graphVersion: sessionData.work_graph.version};
            } else if (at >= request.startedAt + 500) {
              trace.metadataConvergence = {
                error: 'refreshed transcript metadata was not applied within 500ms', generation, baseline, at,
                lastApply: state?.lastApply || null,
                requests: trace.requests.map(request => ({path: request.path, status: request.status, responseAt: request.responseAt, cache: request.body?.cache || null})),
              };
            }
          };
          const markRow = () => {
            if (trace.firstRowAt === null) {
              const row = Array.from(document.querySelectorAll('#panel-__tabber__ .file-tree-row[data-tabber-type="normal"]'))
                .find(candidate => candidate.textContent.includes(targetSession));
              if (row) {
                trace.firstRowAt = performance.now();
                trace.firstRowText = row.textContent;
              }
            }
            observeConvergence();
          };
          new MutationObserver(markRow).observe(document, {subtree: true, childList: true});
          const sampleFrame = () => {
            markRow();
            if (!trace.metadataConvergence) requestAnimationFrame(sampleFrame);
          };
          requestAnimationFrame(sampleFrame);
          const originalFetch = window.fetch.bind(window);
          window.fetch = async (...args) => {
            const requestUrl = new URL(typeof args[0] === 'string' ? args[0] : args[0].url, location.href);
            const isTracked = requestUrl.pathname === '/api/activity' || requestUrl.pathname === '/api/session-metadata';
            if (!isTracked) return originalFetch(...args);
            const startedAt = performance.now();
            const request = {ordinal: trace.requests.length, path: requestUrl.pathname, startedAt, responseAt: null, status: null, body: null};
            trace.requests.push(request);
            const response = await originalFetch(...args);
            request.responseAt = performance.now();
            request.status = response.status;
            response.clone().json().then(body => {
              request.body = body;
              if (request.path === '/api/activity'
                && (!trace.activity || request.ordinal > trace.activity.ordinal)) trace.activity = request;
              observeConvergence();
            });
            return response;
          };
          markRow();
        })();
    """.replace("__TARGET_SESSION__", json.dumps(session))


def main(binary: str) -> None:
    with tempfile.TemporaryDirectory(prefix="yolomux-go-e2e-") as tmp:
        root = Path(tmp)
        config = root / "config"
        config.mkdir()
        fixture_home = root / "home"
        fixture_home.mkdir()
        private_environment_roots = {
            "XDG_CONFIG_HOME": root / "xdg-config",
            "XDG_DATA_HOME": root / "xdg-data",
            "XDG_STATE_HOME": root / "xdg-state",
            "XDG_CACHE_HOME": root / "xdg-cache",
            "XDG_RUNTIME_DIR": root / "xdg-runtime",
            "CODEX_HOME": root / "codex-home",
            product_env_name('CODEX_HOME'): root / "codex-home",
            "CLAUDE_CONFIG_DIR": root / "claude-config",
            "TMPDIR": root / "tmp",
            product_env_name('CLAUDE_TRANSCRIPT_ROOT'): root / "claude-transcripts",
            product_env_name('CODEX_TRANSCRIPT_ROOT'): root / "codex-transcripts",
            product_env_name('OPENCODE_DATABASE'): root / "opencode.db",
        }
        for key, private_root in private_environment_roots.items():
            if key.endswith("DATABASE"):
                private_root.parent.mkdir(parents=True, exist_ok=True)
            else:
                private_root.mkdir(parents=True, exist_ok=True)
        (config / "auth.yaml").write_text("users:\n  - username: e2e\n    password: test-password\n    role: admin\n", encoding="utf-8")
        (config / "auth-cookie-secret").write_text("11" * 32, encoding="ascii")
        port = 0
        tmux_socket_path = short_tmux_socket_path("yogo")
        cleanup_errors = []
        tmux_socket_cleaned = False
        server_log_paths = []

        def cleanup_tmux_socket_dir():
            nonlocal tmux_socket_cleaned
            if tmux_socket_cleaned:
                return
            try:
                shutil.rmtree(tmux_socket_path.parent)
            except FileNotFoundError:
                pass
            except OSError as error:
                message = f"tmux socket directory cleanup failed for {tmux_socket_path.parent}: {error}"
                cleanup_errors.append(message)
                print(message, file=sys.stderr)
                return
            if tmux_socket_path.parent.exists():
                message = f"tmux socket directory remained after cleanup: {tmux_socket_path.parent}"
                cleanup_errors.append(message)
                print(message, file=sys.stderr)
                return
            tmux_socket_cleaned = True

        atexit.register(cleanup_tmux_socket_dir)
        tmux_socket = str(tmux_socket_path)
        tmux = ("tmux", "-S", tmux_socket)
        sessions = [f"yo-go-e2e-{os.getpid()}-{index}" for index in range(3)]
        session = sessions[0]
        source_root = resolve_source_root()
        private_source_root = root / "server-root"
        shutil.copytree(source_root / "static", private_source_root / "static")
        expected_bundle_hashes = {
            "yomux.js": sha256_file(private_source_root / "static" / "yomux.js"),
            "yomux.css": sha256_file(private_source_root / "static" / "yomux.css"),
        }
        binary_hash = sha256_file(Path(binary))
        shutil.copytree(source_root / "tools", private_source_root / "tools")
        shutil.copytree(source_root / "yomux_lib", private_source_root / "yomux_lib")
        shutil.copytree(source_root / "assets/runtime", private_source_root / "assets/runtime")
        finder_root = private_source_root / "finder-fixture"
        (finder_root / "nested").mkdir(parents=True)
        (finder_root / "note.md").write_text("hello from finder\n", encoding="utf-8")
        (finder_root / "ui-remove-me.txt").write_text("delete through Finder\n", encoding="utf-8")
        (finder_root / "index.html").write_text("<script>document.body.textContent = 'preview'</script>\n", encoding="utf-8")
        (finder_root / "nested" / "needle.txt").write_text("nested search hit\n", encoding="utf-8")
        source_file = finder_root / "editor.go"
        source_original = "package main\n\nfunc main() {}\n"
        source_file.write_text(source_original, encoding="utf-8")
        markdown_file = finder_root / "editor.md"
        markdown_original = "# Editor\n\n<!-- hidden metadata -->\n\nA paragraph before the upload.\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n<u>under</u> and <mark>marked</mark>.\n"
        markdown_file.write_text(markdown_original, encoding="utf-8")
        unsupported_markdown_file = finder_root / "unsupported.md"
        unsupported_markdown_file.write_text("# Unsupported\n\n<span>literal HTML</span>\n", encoding="utf-8")
        git_root = finder_root / "git-fixture"
        git_root.mkdir()
        git_note = git_root / "note.txt"
        git_note.write_text("base\n", encoding="utf-8")
        subprocess.run(("git", "init", "-q", str(git_root)), check=True)
        subprocess.run(("git", "-C", str(git_root), "config", "user.email", "e2e@example.invalid"), check=True)
        subprocess.run(("git", "-C", str(git_root), "config", "user.name", "YOmux E2E"), check=True)
        subprocess.run(("git", "-C", str(git_root), "add", "note.txt"), check=True)
        subprocess.run(("git", "-C", str(git_root), "commit", "-qm", "base"), check=True)
        subprocess.run(("git", "-C", str(git_root), "branch", "-M", "e2e-feature"), check=True)
        git_note.write_text("changed\n", encoding="utf-8")
        subprocess.run(("git", "-C", str(git_root), "commit", "-qam", "changed"), check=True)
        for index in range(64):
            (git_root / "history.txt").write_text(f"history {index}\n", encoding="utf-8")
            subprocess.run(("git", "-C", str(git_root), "add", "history.txt"), check=True)
            subprocess.run(("git", "-C", str(git_root), "commit", "-qm", f"history {index}"), check=True)
        git_note.write_text("changed 63\n", encoding="utf-8")
        subprocess.run(("git", "-C", str(git_root), "add", "note.txt"), check=True)
        subprocess.run(("git", "-C", str(git_root), "commit", "-qm", "restore note baseline"), check=True)
        git_note.write_text("working\n", encoding="utf-8")
        git_head = subprocess.check_output(("git", "-C", str(git_root), "rev-parse", "HEAD"), text=True).strip()
        unreadable_file = root / "unreadable.txt"
        unreadable_file.write_text("permission denied\n", encoding="utf-8")
        unreadable_file.chmod(0)
        agent_bin = fixture_home / "bin"
        agent_bin.mkdir()
        launch_record = root / "launch-record.txt"
        launcher_source = """#!/bin/sh
case "$1" in
  --version|auth|login) exit 0 ;;
esac
if [ -n "${YOMUX_E2E_LAUNCH_RECORD:-}" ]; then
  printf '%s %s\\n' "$0" "$*" >> "$YOMUX_E2E_LAUNCH_RECORD"
fi
exec /bin/sleep 300
"""
        for name in ("bash", "claude", "codex"):
            launcher = agent_bin / name
            launcher.write_text(launcher_source, encoding="utf-8")
            launcher.chmod(0o755)
        precreated_bin = fixture_home / "precreated-bin"
        precreated_bin.mkdir()
        precreated_claude_launcher = precreated_bin / "claude"
        precreated_claude_launcher.symlink_to("/bin/sleep")
        created_sessions = []
        for index, session_name in enumerate(sessions):
            if subprocess.run((*tmux, "has-session", "-t", session_name), check=False, stderr=subprocess.DEVNULL).returncode != 0:
                command = [*tmux, "new-session", "-d", "-s", session_name, "-c", str(git_root)]
                if index == 0:
                    command.extend((str(precreated_claude_launcher), "300"))
                subprocess.run(command, check=True)
                created_sessions.append(session_name)
        second_window = [*tmux, "new-window", "-d", "-t", f"={session}:", "-n", "claude-second", "-c", str(git_root), str(precreated_claude_launcher), "300"]
        subprocess.run(second_window, check=True)
        subprocess.run((*tmux, "select-window", "-t", f"={session}:0"), check=True)
        agent_pane_pid = subprocess.check_output((*tmux, "display-message", "-p", "-t", f"={session}:0.0", "#{pane_pid}"), text=True).strip()
        second_agent_pane_pid = subprocess.check_output((*tmux, "display-message", "-p", "-t", f"={session}:1.0", "#{pane_pid}"), text=True).strip()
        second_agent_pane_target = subprocess.check_output((*tmux, "display-message", "-p", "-t", f"={session}:1.0", "#{pane_id}"), text=True).strip()
        claude_transcript = require_product_env('CLAUDE_TRANSCRIPT_ROOT', environ=private_environment_roots) / "fixture" / "claude-session.jsonl"
        claude_transcript.parent.mkdir(parents=True)
        claude_transcript.write_text(
            json.dumps({"type": "assistant", "cwd": str(git_root), "message": {"role": "assistant", "content": "Docker transcript first"}}) + "\n",
            encoding="utf-8",
        )
        second_claude_transcript = require_product_env('CLAUDE_TRANSCRIPT_ROOT', environ=private_environment_roots) / "fixture" / "claude-session-2.jsonl"
        second_claude_transcript.write_text(
            json.dumps({"type": "assistant", "cwd": str(git_root), "message": {"role": "assistant", "content": "Docker transcript second"}}) + "\n",
            encoding="utf-8",
        )
        claude_metadata = fixture_home / ".claude" / "sessions" / f"{agent_pane_pid}.json"
        claude_metadata.parent.mkdir(parents=True)
        claude_metadata.write_text(json.dumps({"sessionId": "claude-session", "cwd": str(git_root)}), encoding="utf-8")
        (fixture_home / ".claude" / "sessions" / f"{second_agent_pane_pid}.json").write_text(
            json.dumps({"sessionId": "claude-session-2", "cwd": str(git_root)}), encoding="utf-8",
        )

        def stop_process(process, label, force=False):
            if process is None or process.poll() is not None:
                return
            try:
                (process.kill if force else process.terminate)()
            except ProcessLookupError:
                pass
            except OSError as error:
                cleanup_errors.append(f"{label} termination failed: {error}")
                return
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                except OSError as error:
                    cleanup_errors.append(f"{label} forced termination failed: {error}")
                    return
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    cleanup_errors.append(f"{label} survived its cleanup deadline (pid {process.pid})")
            if process.poll() is None:
                cleanup_errors.append(f"{label} remained alive after cleanup (pid {process.pid})")

        def cleanup_tmux_session(session_name):
            result = subprocess.run((*tmux, "kill-session", "-t", session_name), check=False, capture_output=True, text=True)
            probe = subprocess.run((*tmux, "has-session", "-t", session_name), check=False, capture_output=True, text=True)
            if probe.returncode == 0:
                detail = result.stderr.strip() or result.stdout.strip() or "kill-session returned without retiring the session"
                cleanup_errors.append(f"tmux session {session_name} survived cleanup: {detail}")
            elif probe.returncode != 1:
                detail = probe.stderr.strip() or probe.stdout.strip() or f"tmux has-session exited {probe.returncode}"
                cleanup_errors.append(f"could not prove tmux session {session_name} was retired: {detail}")

        def start_server(dangerously_yolo=True):
            nonlocal port
            generation = len(list(root.glob("server-*.txt"))) + 1
            log_path = root / f"server-{generation}.txt"
            server_log_paths.append(log_path)
            log_file = log_path.open("w", encoding="utf-8")
            environment = synthetic_server_environment({
                "HOME": str(fixture_home),
                **{key: str(value) for key, value in private_environment_roots.items()},
                # This terminal/browser contract uses seeded stats facts and must
                # never scan the host's client databases or transcripts.
                product_env_name('STATS_DISABLE_COLLECTORS'): "1",
            })
            environment[product_env_name('TMUX_SOCKET')] = tmux_socket
            environment["PATH"] = str(agent_bin) + os.pathsep + environment.get("PATH", "")
            environment[product_env_name('E2E_LAUNCH_RECORD')] = str(launch_record)
            subprocess.run((*tmux, "set-environment", "-g", "PATH", environment["PATH"]), env=environment, check=True)
            subprocess.run((*tmux, "set-environment", "-g", product_env_name('E2E_LAUNCH_RECORD'), str(launch_record)), env=environment, check=True)
            # Keep this deliberate backpressure fixture private to the uncached browser contract:
            # the normal queue remains 256, while Chromium proves that an evicted resource reaches
            # the browser's repair path instead of silently disappearing.
            environment[product_env_name('GO_EVENT_QUEUE_SIZE')] = "1"
            environment[product_env_name('GO_EVENT_STREAM_DELAY_MS')] = "500"
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("0.0.0.0", port))
                probe.listen(128)
                port = int(probe.getsockname()[1])
                command = [binary, "--host", "0.0.0.0", "--port", str(port), "--listen-fd", str(probe.fileno()), "--root", str(private_source_root), "--config", str(config)]
                if dangerously_yolo:
                    command.append("--dangerously-yolo")
                process = subprocess.Popen(command, env=environment, stdout=log_file, stderr=subprocess.STDOUT, pass_fds=(probe.fileno(),))
            log_file.close()
            for _ in range(80):
                try:
                    with urllib.request.urlopen(f"https://127.0.0.1:{port}/login", context=ssl._create_unverified_context(), timeout=1):
                        pass
                    return process
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.1)
            stop_process(process, "Go server startup process")
            for session_name in created_sessions:
                cleanup_tmux_session(session_name)
            log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-12000:]
            raise SystemExit(f"Go server did not become ready; log={log_tail!r}")

        proc = start_server()
        driver = None
        driver_lease = None

        def stop_server(force=False):
            nonlocal proc
            stop_process(proc, "Go server", force=force)
            proc = None

        def server_diagnostic():
            process_state = "not started" if proc is None else f"pid={proc.pid} exit={proc.poll()}"
            log_tails = []
            for log_path in server_log_paths:
                try:
                    log_tails.append(f"{log_path.name}: {log_path.read_text(encoding='utf-8', errors='replace')[-4000:]!r}")
                except OSError as error:
                    log_tails.append(f"{log_path.name}: read failed: {error}")
            return f"server={process_state}; logs={log_tails!r}"

        def assert_server_identity(driver, process):
            identity = driver.execute_async_script("""
                const done = arguments[arguments.length - 1];
                fetch('/api/system-status', {cache: 'no-store', credentials: 'same-origin'})
                  .then(response => response.json().then(body => done({status: response.status, body})))
                  .catch(error => done({error: String(error)}));
            """)
            if identity.get("status") != 200 or identity.get("body", {}).get("server", {}).get("pid") != process.pid:
                raise SystemExit(f"Go Phase 3 listener identity failed: expected pid {process.pid}, got {identity}")

        def tmux_window_dimensions():
            output = subprocess.check_output(
                (*tmux, "display-message", "-p", "-t", f"={session}:0", "#{window_width}\\t#{window_height}"),
                text=True,
            ).strip()
            width, height = output.split("\\t", 1)
            return int(width), int(height)

        def set_pty_dimensions(fd, rows, cols):
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

        try:
            options = Options()
            chrome_binary = os.environ.get("CHROME_BIN") or shutil.which("chromium") or shutil.which("google-chrome")
            if not chrome_binary:
                raise SystemExit("Chromium or Google Chrome is unavailable")
            options.binary_location = chrome_binary
            for arg in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--ignore-certificate-errors", "--window-size=1200,800"):
                options.add_argument(arg)
            driver_path = os.environ.get("CHROMEDRIVER_BIN") or shutil.which("chromedriver")
            served_bundle_hashes = {}
            for asset, expected in expected_bundle_hashes.items():
                asset_url = f"https://127.0.0.1:{port}/static/{asset}"
                try:
                    with urllib.request.urlopen(
                        asset_url,
                        context=ssl._create_unverified_context(),
                        timeout=5,
                    ) as response:
                        served = response.read()
                except (OSError, urllib.error.URLError, TimeoutError) as error:
                    raise SystemExit(
                        f"served bundle request failed for {asset_url}: {error}; {server_diagnostic()}"
                    ) from error
                actual = sha256_bytes(served)
                if actual != expected:
                    raise SystemExit(
                        f"served {asset} hash mismatch: expected {expected}, got {actual}"
                    )
                served_bundle_hashes[asset] = actual
            if driver_path:
                driver = webdriver.Chrome(service=Service(executable_path=driver_path), options=options)
            else:
                driver = webdriver.Chrome(options=options)
            driver_lease = WebDriverLease.from_driver(driver)
            try:
                driver.get(f"https://127.0.0.1:{port}/login")
                driver.find_element("name", "username").send_keys("e2e")
                driver.find_element("name", "password").send_keys("test-password")
                driver.find_element("css selector", "button[type=submit]").click()
                time.sleep(0.5)
                driver.execute_script("localStorage.clear(); sessionStorage.clear();")
                driver.get(f"https://127.0.0.1:{port}/?sessions={session}&layout=left&tabs=left:{session}")
                WebDriverWait(driver, 15).until(lambda browser: browser.execute_script("""
                    try {
                      return typeof saveSettingsPatch === 'function' && typeof layoutSlots === 'object'
                        && typeof initialSetting === 'function' && document.querySelector('#grid .xterm') !== null;
                    } catch (error) {
                      if (error instanceof ReferenceError) return false;
                      throw error;
                    }
                """))
                settings_result = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const waitFor = predicate => new Promise((resolve, reject) => {
                      const deadline = performance.now() + 10000;
                      const poll = () => {
                        try {
                          if (predicate()) { resolve(); return; }
                        } catch (error) { reject(error); return; }
                        if (performance.now() >= deadline) { reject(new Error('settings UI did not converge')); return; }
                        setTimeout(poll, 25);
                      };
                      poll();
                    });
                    (async () => {
                      try {
                        await saveSettingsPatch({
                          general: {language: 'ja'},
                          appearance: {theme: 'light', active_color: 'blue', ui_font_size: 16},
                          file_explorer: {indexed_dirs: ['/tmp']},
                        });
                        await waitFor(() => document.body.classList.contains('theme-light')
                          && document.documentElement.lang === 'ja'
                          && initialSetting('appearance.active_color', '') === 'blue'
                          && getComputedStyle(document.documentElement).getPropertyValue('--active-accent').trim() !== '');
                        const response = await fetch('/api/settings', {cache: 'no-store', credentials: 'same-origin'});
                        const body = await response.json();
                        done({
                          status: response.status,
                          language: body.settings?.general?.language,
                          theme: body.settings?.appearance?.theme,
                          activeColor: body.settings?.appearance?.active_color,
                          uiFontSize: body.settings?.appearance?.ui_font_size,
                          indexedDirs: body.settings?.file_explorer?.indexed_dirs,
                          bodyTheme: document.body.className,
                          documentLanguage: document.documentElement.lang,
                        });
                      } catch (error) {
                        done({
                          error: String(error?.stack || error),
                          accessRole: typeof accessRole === 'undefined' ? null : accessRole,
                          readOnly: typeof readOnlyMode === 'undefined' ? null : readOnlyMode,
                          bodyClass: document.body.className,
                          documentLanguage: document.documentElement.lang,
                        });
                      }
                    })();
                """)
                if (settings_result.get("error") or settings_result.get("status") != 200 or settings_result.get("language") != "ja" or settings_result.get("theme") != "light" or settings_result.get("activeColor") != "blue" or settings_result.get("uiFontSize") != 16 or settings_result.get("indexedDirs") != ["/tmp"] or settings_result.get("documentLanguage") != "ja"):
                    raise SystemExit(f"Go Chromium settings parity failed: {settings_result}")
                settings_restore = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    (async () => {
                      try {
                        await saveSettingsPatch({
                          general: {language: 'en'},
                          appearance: {theme: 'dark', active_color: 'green', ui_font_size: 14},
                          file_explorer: {indexed_dirs: []},
                        });
                        const deadline = performance.now() + 10000;
                        const poll = () => document.documentElement.lang === 'en' && document.body.classList.contains('theme-dark')
                          ? done({ok: true})
                          : performance.now() >= deadline
                            ? done({error: 'settings restore did not converge', language: document.documentElement.lang, bodyClass: document.body.className})
                            : setTimeout(poll, 25);
                        poll();
                      } catch (error) {
                        done({error: String(error?.stack || error)});
                      }
                    })();
                """)
                if settings_restore.get("error"):
                    raise SystemExit(f"Go Chromium settings restore failed: {settings_restore}")
                deadline = time.time() + 15
                while time.time() < deadline and not driver.find_elements("css selector", f"#term-{session} .xterm"):
                    time.sleep(0.1)
                result = driver.execute_script("""
                    if (typeof terminals === 'undefined') return {error: 'terminal runtime did not load', url: location.href, title: document.title};
                    const item = terminals.get(arguments[0]);
                    if (!item?.term || item.socket?.readyState !== WebSocket.OPEN) return {
                        error: 'terminal did not connect',
                        url: location.href,
                        body: document.body.innerText.slice(0, 300),
                        bootstrap: (document.getElementById('yomux-bootstrap') || document.getElementById('yolomux-bootstrap'))?.textContent?.slice(0, 2000) || null,
                        activeSessions: typeof activeSessions !== 'undefined' ? activeSessions : null,
                        layoutSlots: typeof layoutSlots !== 'undefined' ? layoutSlots : null,
                        terminal: Boolean(item?.term),
                        socket: item?.socket?.readyState || null,
                    };
                    setBadConnectionCursorState(false);
                    const before = {cols: item.term.cols, rows: item.term.rows, cursorBlink: item.term.options.cursorBlink, body: document.body.className};
                    item.container.style.width = '500px';
                    item.container.style.height = '200px';
                    fitTerminal(arguments[0], {force: true, claim: true});
                    return {before, after: {cols: item.term.cols, rows: item.term.rows, cursorBlink: item.term.options.cursorBlink}};
                """, session)
                if result.get("error") or result["after"]["cols"] >= result["before"]["cols"] or result["after"]["rows"] >= result["before"]["rows"] or result["after"]["cursorBlink"] is not True:
                    diagnostics = driver.execute_async_script("""
                        const done = arguments[arguments.length - 1];
                        const session = arguments[0];
                        Promise.all(['/api/tmux-session-exists?session=' + encodeURIComponent(session), '/api/ensure-session?session=' + encodeURIComponent(session)]
                          .map(url => fetch(url, {method: url.includes('ensure-session') ? 'POST' : 'GET', cache: 'no-store', credentials: 'same-origin'})
                            .then(response => response.text().then(body => ({url, status: response.status, body})))
                            .catch(error => ({url, error: String(error)}))))
                          .then(api => done({api, performance: performance.getEntriesByType('resource').map(entry => entry.name).filter(name => name.includes('/api/')).slice(-30), status: document.getElementById('status')?.innerText || ''}))
                          .catch(error => done({error: String(error)}));
                    """, session)
                    result["diagnostics"] = diagnostics
                    result["browser_log"] = driver.get_log("browser")
                    raise SystemExit(f"Go Chromium terminal E2E failed: {result}")
                # A second, larger non-ignore-size client is the regression fixture for the shared
                # tmux window. The focused browser client must take authority, otherwise
                # `window-size largest` leaves tmux at the larger sibling dimensions even though
                # xterm itself resized locally.
                resize_fence = driver.execute_async_script("""
                  const done = arguments[arguments.length - 1];
                  const item = terminals.get(arguments[0]);
                  const txn = 7110001;
                  const listener = event => {
                    if (typeof event.data !== 'string') return;
                    let payload; try {payload = JSON.parse(event.data);} catch (_) {return;}
                    if (payload.type !== 'refresh-ack' || payload.txn !== txn) return;
                    item.socket.removeEventListener('message', listener);
                    done({ok: true});
                  };
                  item.socket.addEventListener('message', listener);
                  item.socket.send(JSON.stringify({type: 'refresh', txn}));
                """, session)
                assert resize_fence.get('ok'), resize_fence
                secondary_master, secondary_slave = pty.openpty()
                set_pty_dimensions(secondary_slave, 70, 220)
                secondary_client = subprocess.Popen(
                    (*tmux, "attach-session", "-t", f"={session}:"),
                    stdin=secondary_slave,
                    stdout=secondary_slave,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                    close_fds=True,
                    env={**os.environ, "TERM": "xterm-256color"},
                )
                os.close(secondary_slave)
                try:
                    secondary_deadline = time.time() + 5
                    while time.time() < secondary_deadline:
                        if secondary_client.poll() is not None:
                            error = secondary_client.stderr.read().decode("utf-8", "replace").strip() if secondary_client.stderr else ""
                            raise SystemExit(
                                "secondary tmux resize client exited: "
                                f"{secondary_client.returncode}; stderr={error!r}; "
                                f"sessions={subprocess.run((*tmux, 'list-sessions', '-F', '#S'), capture_output=True, text=True).stdout!r}"
                            )
                        try:
                            secondary_width, secondary_height = tmux_window_dimensions()
                            if secondary_width >= 200 and secondary_height >= 60:
                                break
                        except (subprocess.CalledProcessError, ValueError):
                            pass
                        time.sleep(0.05)
                    else:
                        raise SystemExit(f"secondary tmux client did not establish large window: {tmux_window_dimensions()}")
                    resized = driver.execute_script("""
                        const item = terminals.get(arguments[0]);
                        fitTerminal(arguments[0], {force: true, claim: true});
                        return {cols: item.term.cols, rows: item.term.rows};
                    """, session)
                    resized_width, resized_height = resized["cols"], resized["rows"]
                    dimensions_deadline = time.time() + 5
                    observed_dimensions = None
                    while time.time() < dimensions_deadline:
                        observed_dimensions = tmux_window_dimensions()
                        if observed_dimensions[0] == resized_width and observed_dimensions[1] in (resized_height, resized_height - 1):
                            break
                        time.sleep(0.05)
                    if observed_dimensions[0] != resized_width or observed_dimensions[1] not in (resized_height, resized_height - 1):
                        clients = subprocess.run(
                            (*tmux, "list-clients", "-F", "#{client_tty}\\t#{client_session}\\t#{client_width}\\t#{client_height}\\t#{client_flags}"),
                            capture_output=True,
                            text=True,
                        ).stdout.strip()
                        raise SystemExit(
                            "Go tmux resize E2E failed: "
                            f"xterm={resized_width}x{resized_height}, tmux={observed_dimensions}, "
                            f"secondary={secondary_width}x{secondary_height}, clients={clients!r}"
                        )
                finally:
                    if secondary_client.poll() is None:
                        os.killpg(secondary_client.pid, signal.SIGTERM)
                        try:
                            secondary_client.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            os.killpg(secondary_client.pid, signal.SIGKILL)
                            secondary_client.wait(timeout=5)
                    os.close(secondary_master)
                assert_server_identity(driver, proc)
                refresh = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const button = document.getElementById('refreshMeta');
                    const before = Number(transcriptMetadataState?.generation || 0);
                    if (!button || typeof button.onclick !== 'function') {
                      done({error: 'top-right refresh button is not wired', button: Boolean(button)});
                      return;
                    }
                    button.click();
                    const deadline = performance.now() + 12000;
                    const poll = () => {
                      const generation = Number(transcriptMetadataState?.generation || 0);
                      if (generation > before && transcriptMetadataState?.loading !== true) {
                        done({before, generation, loading: false});
                        return;
                      }
                      if (performance.now() >= deadline) {
                        done({error: 'top-right refresh did not converge', before, generation, loading: transcriptMetadataState?.loading, lastApply: transcriptMetadataState?.lastApply || null});
                        return;
                      }
                      setTimeout(poll, 40);
                    };
                    poll();
                """)
                if refresh.get("error"):
                    refresh["browser_log"] = driver.get_log("browser")
                    raise SystemExit(f"Go top-right refresh E2E failed: {refresh}")
                launch_contract = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const json = async (url, options = {}) => {
                      const response = await fetch(url, {cache: 'no-store', credentials: 'same-origin', ...options});
                      const body = await response.json();
                      return {status: response.status, body};
                    };
                    const cases = [
                      {agent: 'term', terminal: 'bash', dangerous: false, command: 'bash'},
                      {agent: 'claude', terminal: '', dangerous: false, command: 'claude'},
                      {agent: 'claude', terminal: '', dangerous: true, command: 'claude --dangerously-skip-permissions'},
                      {agent: 'codex', terminal: '', dangerous: false, command: 'codex'},
                      {agent: 'codex', terminal: '', dangerous: true, command: 'codex --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust'},
                    ];
                    (async () => {
                      try {
                        const launches = [];
                        for (const [index, item] of cases.entries()) {
                          const plan = await json('/api/create-session-plan');
                          if (plan.status !== 200 || !Number.isSafeInteger(Number(plan.body?.generation))) {
                            throw new Error(`unsafe create-session plan: ${JSON.stringify(plan)}`);
                          }
                          const expectedSession = String(index + 1);
                          if (plan.body?.session !== expectedSession) {
                            throw new Error(`Go create-session plan chose ${JSON.stringify(plan.body?.session)}, want ${expectedSession}`);
                          }
                          const params = new URLSearchParams({
                            agent: item.agent,
                            dangerously_yolo: item.dangerous ? '1' : '0',
                            session: String(plan.body.session),
                            generation: String(plan.body.generation),
                          });
                          if (item.terminal) params.set('terminal', item.terminal);
                          const created = await json('/api/create-session?' + params, {method: 'POST'});
                          if (created.status !== 200 || created.body?.command !== item.command || created.body?.created !== true) {
                            throw new Error(`launch mismatch for ${item.agent}: ${JSON.stringify(created)}`);
                          }
                          launches.push(created.body);
                        }
                        done({launches});
                      } catch (error) {
                        done({error: String(error?.stack || error)});
                      }
                    })();
                """)
                if launch_contract.get("error") or len(launch_contract.get("launches", [])) != 5:
                    raise SystemExit(f"Go File -> New launch contract failed: {launch_contract}")
                for launch in launch_contract["launches"]:
                    created_sessions.append(launch["session"])
                expected_fixture_sessions = sorted(set(sessions) | set(created_sessions))
                expected_launch_commands = {
                    "bash",
                    "claude",
                    "claude --dangerously-skip-permissions",
                    "codex",
                    "codex --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust",
                }
                def read_recorded_commands():
                    if not launch_record.exists():
                        return set()
                    commands = set()
                    for line in launch_record.read_text(encoding="utf-8").splitlines():
                        executable, separator, arguments = line.partition(" ")
                        if separator:
                            commands.add(" ".join((Path(executable).name, arguments.strip())).strip())
                    return commands

                def wait_for_recorded_command(command):
                    launch_deadline = time.time() + 5
                    observed = set()
                    while time.time() < launch_deadline:
                        observed = read_recorded_commands()
                        if command in observed:
                            return observed
                        time.sleep(0.05)
                    raise SystemExit(f"Go File -> New launch command did not reach fixture process: {command!r}, observed={sorted(observed)!r}")

                recorded = set()
                for command in expected_launch_commands:
                    recorded = wait_for_recorded_command(command)

                driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": tabber_reload_trace_source(session)})
                tabber_reload_url = (
                    f"https://127.0.0.1:{port}/?sessions=__tabber__,{session}"
                    f"&layout=row@34(left,right)&tabs=left:__tabber__;right:{session}"
                )
                driver.get(tabber_reload_url)
                try:
                    WebDriverWait(driver, 12).until(lambda browser: browser.execute_script("""
                        const trace = window.__tabberReloadTrace;
                        const session = new URLSearchParams(location.search).get('sessions')?.split(',')[1];
                        const expectedSessions = arguments[0];
                        const state = typeof transcriptMetadataState === 'object' ? transcriptMetadataState : null;
                        const rendered = new Set(Array.from(document.querySelectorAll('#panel-__tabber__ .file-tree-row[data-tabber-type="normal"]'))
                          .map(row => row.dataset.tabberSession));
                        const freshRequest = trace?.requests?.some(request =>
                          request.body?.cache?.stale === false
                          && request.body?.cache?.refreshing === false
                          && expectedSessions.every(name => request.body?.session_order?.includes(name))
                          && request.body?.sessions?.[session]?.work_graph?.version === 1);
                        // Completed metadata is also published over SSE. Require that
                        // exact generation in the model and every roster row in the DOM.
                        const freshPush = state?.payload?.metadata_identity?.generation === state?.generation
                          && state?.payload?.cache?.stale !== true && state?.payload?.cache?.refreshing !== true
                          && expectedSessions.every(name => state?.payload?.session_order?.includes(name))
                          && state?.payload?.sessions?.[session]?.work_graph?.version === 1;
                        return Boolean((freshRequest || freshPush) && expectedSessions.every(name => rendered.has(name)));
                    """, expected_fixture_sessions))
                except TimeoutException as error:
                    trace = driver.execute_script("""
                        const session = new URLSearchParams(location.search).get('sessions')?.split(',')[1];
                        const expected = arguments[0];
                        return {
                          readyState: document.readyState,
                          expectedRoster: expected,
                          browserRoster: typeof sessionNames !== 'undefined' ? sessionNames : null,
                          metadataState: typeof transcriptMetadataState !== 'undefined' ? {
                            generation: transcriptMetadataState.generation,
                            lastApply: transcriptMetadataState.lastApply,
                            topology: transcriptMetadataState.payload?.topology_generation,
                            roster: transcriptMetadataState.payload?.session_order,
                          } : null,
                          domRows: Array.from(document.querySelectorAll('#panel-__tabber__ .file-tree-row')).map(row => ({text: row.textContent, type: row.dataset.tabberType})),
                          requests: (window.__tabberReloadTrace?.requests || []).map(request => ({
                            path: request.path, status: request.status, cache: request.body?.cache,
                            roster: request.body?.session_order,
                            missing: expected.filter(name => !request.body?.session_order?.includes(name)),
                            graphVersion: request.body?.sessions?.[session]?.work_graph?.version,
                            topology: request.body?.topology_generation,
                            metadataGeneration: request.body?.metadata_generation,
                            graphVersions: Object.fromEntries(Object.entries(request.body?.sessions || {}).map(([name, info]) => [name, info.work_graph?.version])),
                          })),
                        };
                    """, expected_fixture_sessions)
                    trace["browser_errors"] = driver.get_log("browser")
                    trace["tmux_roster"] = subprocess.run(
                        (*tmux, "list-sessions", "-F", "#{session_name}"),
                        check=False, capture_output=True, text=True,
                    ).stdout.splitlines()
                    raise SystemExit("Tabber initial load did not publish a fresh work graph for the complete fixture roster before the warm reload: " + json.dumps(trace, sort_keys=True)) from error
                driver.refresh()
                try:
                    WebDriverWait(driver, 12).until(lambda browser: browser.execute_script("""
                        const trace = window.__tabberReloadTrace;
                        const session = new URLSearchParams(location.search).get('sessions')?.split(',')[1];
                        return Boolean(trace?.activity?.body && trace.firstRowAt !== null
                          && trace.metadataConvergence
                          && trace.requests.some(request => request.body?.sessions?.[session]));
                    """))
                except TimeoutException as error:
                    trace = driver.execute_script("""
                        const session = new URLSearchParams(location.search).get('sessions')?.split(',')[1];
                        const current = window.__tabberReloadTrace;
                        return {
                          url: location.href,
                          readyState: document.readyState,
                          firstRowAt: current?.firstRowAt ?? null,
                          firstRowText: current?.firstRowText || '',
                          requests: (current?.requests || []).map(request => ({
                            path: request.path,
                            status: request.status,
                            responseAt: request.responseAt,
                            hasSession: Boolean(request.body?.sessions?.[session]),
                            cache: request.body?.cache,
                            graphVersion: request.body?.sessions?.[session]?.work_graph?.version,
                          })),
                        };
                    """)
                    raise SystemExit(f"Tabber reload trace timed out: {json.dumps(trace, sort_keys=True)}") from error
                tabber_reload_trace = driver.execute_script("return window.__tabberReloadTrace")
                tabber_reload_activity = tabber_reload_trace["activity"]
                tabber_reload_duration_ms = tabber_reload_activity["responseAt"] - tabber_reload_activity["startedAt"]
                tabber_reload_sources = [
                    request for request in tabber_reload_trace["requests"]
                    if request["responseAt"] is not None
                    and request["responseAt"] <= tabber_reload_trace["firstRowAt"]
                    and request.get("body", {}).get("sessions", {}).get(session)
                ]
                tabber_reload_source = min(tabber_reload_sources, key=lambda request: request["responseAt"], default=None)
                if tabber_reload_source is None:
                    raise SystemExit(f"Tabber row appeared without a preceding matching metadata response: {tabber_reload_trace}")
                tabber_reload_source_duration_ms = tabber_reload_source["responseAt"] - tabber_reload_source["startedAt"]
                tabber_reload_first_paint_ms = tabber_reload_trace["firstRowAt"] - tabber_reload_source["startedAt"]
                tabber_reload_body = tabber_reload_source["body"]
                tabber_reload_session = tabber_reload_body.get("sessions", {}).get(session, {})
                tabber_reload_convergence = tabber_reload_trace["metadataConvergence"]
                if (
                    tabber_reload_activity["status"] != 200
                    or tabber_reload_activity["responseAt"] - tabber_reload_activity["startedAt"] >= 500
                    or tabber_reload_body.get("cache", {}).get("hit") is not True
                    or tabber_reload_first_paint_ms < tabber_reload_source_duration_ms
                    or tabber_reload_source_duration_ms >= 500
                    or tabber_reload_duration_ms >= 500
                    or tabber_reload_first_paint_ms >= 500
                    or tabber_reload_session.get("work_graph", {}).get("version") != 1
                    or tabber_reload_convergence.get("error")
                    or tabber_reload_convergence["at"] - tabber_reload_source["startedAt"] >= 500
                ):
                    raise SystemExit(
                        "Chromium reload did not render cached Tabber data within 500ms or refresh stale metadata within 500ms: "
                        + json.dumps({
                            "row_source": tabber_reload_source["path"],
                            "row_source_ms": tabber_reload_source_duration_ms,
                            "activity_ms": tabber_reload_duration_ms,
                            "first_row_ms": tabber_reload_first_paint_ms,
                            "cache": tabber_reload_body.get("cache"),
                            "graph_version": tabber_reload_session.get("work_graph", {}).get("version"),
                            "metadata_convergence": tabber_reload_convergence,
                        }, sort_keys=True)
                    )
                metadata_converged_ms = None if tabber_reload_convergence.get("skipped") else tabber_reload_convergence["at"] - tabber_reload_source["startedAt"]
                print(f"Go browser Tabber reload: {json.dumps({'cached_source_ms': tabber_reload_source_duration_ms, 'first_row_ms': tabber_reload_first_paint_ms, 'metadata_converged_ms': metadata_converged_ms, 'metadata_generation': tabber_reload_convergence['generation'], 'metadata_refresh_skipped': tabber_reload_convergence.get('skipped')}, sort_keys=True)}")
                surfaces = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const session = arguments[0];
                    const expectedPath = arguments[1];
                    const tabberReloadDurationMs = Number(arguments[2]);
                    const tabberReloadFirstPaintMs = Number(arguments[3]);
                    const activityTrace = window.__tabberReloadTrace?.activity || null;
                    const tabberActivityResponse = activityTrace ? {
                      status: activityTrace.status,
                      body: activityTrace.body,
                    } : null;
                    const tabberFirstPaintMs = tabberReloadFirstPaintMs;
                    const waitFor = (predicate, description, timeoutMs = 12000) => new Promise((resolve, reject) => {
                      const deadline = performance.now() + timeoutMs;
                      const poll = () => {
                        try {
                          const value = predicate();
                          if (value) { resolve(value); return; }
                        } catch (error) { reject(error); return; }
                        if (performance.now() >= deadline) { reject(new Error(`${description} timed out`)); return; }
                        setTimeout(poll, 40);
                      };
                      poll();
                    });
                    const waitForAsync = (predicate, description, timeoutMs = 12000) => new Promise((resolve, reject) => {
                      const deadline = performance.now() + timeoutMs;
                      const poll = async () => {
                        try {
                          const value = await predicate();
                          if (value) { resolve(value); return; }
                        } catch (error) { reject(error); return; }
                        if (performance.now() >= deadline) { reject(new Error(`${description} timed out`)); return; }
                        setTimeout(poll, 40);
                      };
                      poll();
                    });
                    const json = async (url, options = {}) => {
                      const response = await fetch(url, {cache: 'no-store', credentials: 'same-origin', ...options});
                      const body = await response.json();
                      return {status: response.status, body};
                    };
                    (async () => {
                      try {
                        let metadata = await json('/api/session-metadata?force=1');
                        if (metadata.status !== 200) throw new Error(`session metadata status ${metadata.status}`);
                        // This direct probe owns the same response that the production refresh path
                        // applies. Apply it before inspecting rendered surfaces; otherwise the test
                        // can validate fresh graph bytes while the DOM still shows an earlier empty
                        // bootstrap snapshot.
                        if (typeof applyTranscriptsPayload !== 'function') throw new Error('session metadata apply helper is unavailable');
                        await applyTranscriptsPayload(metadata.body, {refreshAuto: false, refreshContext: false, refreshActivity: false, source: 'e2e-probe'});
                        const hasRepository = payload => Object.values(payload?.sessions?.[session]?.work_graph?.git_worktrees || {}).length > 0;
                        const pending = metadata.body.cache?.pending_identity || {};
                        const pendingGeneration = Number(pending.generation || 0);
                        if (pendingGeneration) {
                          metadata = await waitForAsync(async () => {
                            const current = await json('/api/session-metadata');
                            const identity = current.body.metadata_identity || {};
                            return current.status === 200 && identity.epoch === pending.epoch && Number(identity.generation || 0) >= pendingGeneration && hasRepository(current.body) ? current : null;
                          }, 'published session work graph');
                          await applyTranscriptsPayload(metadata.body, {refreshAuto: false, refreshContext: false, refreshActivity: false, source: 'e2e-probe'});
                        }
                        const freshActivityResponse = await json('/api/activity?hours=24&visible=1&scope=all');
                        const activitySession = freshActivityResponse.body.sessions?.[session] || {};
                        if (tabberActivityResponse.status !== 200
                            || tabberActivityResponse.body.cache?.hit !== true
                            || freshActivityResponse.status !== 200
                            || freshActivityResponse.body.cache?.hit !== true
                            || freshActivityResponse.body.cache?.stale !== false
                            || activitySession.work_graph?.version !== 1
                            || tabberReloadDurationMs >= 500
                            || tabberFirstPaintMs === null
                            || tabberFirstPaintMs >= 500) {
                          throw new Error(`warm Tabber activity did not return and render within 500ms or converge to a fresh snapshot: ${JSON.stringify({status: tabberActivityResponse.status, durationMs: tabberReloadDurationMs, firstPaintMs: tabberFirstPaintMs, warmCache: tabberActivityResponse.body.cache, freshStatus: freshActivityResponse.status, freshCache: freshActivityResponse.body.cache, graphVersion: activitySession.work_graph?.version})}`);
                        }
                        const info = metadata.body.sessions?.[session] || {};
                        const graph = info.work_graph || {};
                        const repos = Object.values(graph.git_worktrees || {});
                        if (graph.version !== 1 || !repos.length) throw new Error(`metadata graph missing repository: ${JSON.stringify(info)}`);
                        const agents = Array.isArray(info.agents) ? info.agents : [];
                        const firstAgent = agents.find(agent => String(agent?.window || '') === '0');
                        const secondAgent = agents.find(agent => String(agent?.window || '') === '1');
                        if (!firstAgent?.pane_target || !secondAgent?.pane_target) throw new Error(`multi-window agents missing: ${JSON.stringify(agents)}`);
                        const transcript = async query => json(`/api/transcript?session=${encodeURIComponent(session)}&${query}`);
                        const firstTranscript = await transcript(`pane_target=${encodeURIComponent(firstAgent.pane_target)}`);
                        const secondTranscript = await transcript(`pane_target=${encodeURIComponent(secondAgent.pane_target)}`);
                        const activeTranscript = await json(`/api/transcript?session=${encodeURIComponent(session)}`);
                        const secondWindowContext = await json(`/api/context-items?session=${encodeURIComponent(session)}&window=1&messages=200`);
                        if (firstTranscript.status !== 200 || !firstTranscript.body.text.includes('Docker transcript first')) throw new Error(`first pane transcript mismatch: ${JSON.stringify(firstTranscript)}`);
                        if (secondTranscript.status !== 200 || !secondTranscript.body.text.includes('Docker transcript second')) throw new Error(`second pane transcript mismatch: ${JSON.stringify(secondTranscript)}`);
                        if (activeTranscript.status !== 200 || !activeTranscript.body.text.includes('Docker transcript first')) throw new Error(`active pane transcript mismatch: ${JSON.stringify(activeTranscript)}`);
                        if (secondWindowContext.status !== 200 || !secondWindowContext.body.items.some(item => item.text.includes('Docker transcript second'))) throw new Error(`window context mismatch: ${JSON.stringify(secondWindowContext)}`);
                        await waitFor(() => {
                          const text = document.getElementById(`meta-${session}`)?.textContent || '';
                          return text.includes('e2e-feature') && text.includes('git-fixture');
                        }, 'Info Bar metadata');
                        const tab = document.querySelector(`[data-tab="${CSS.escape(session)}"]`);
                        showTabContextMenu(session, 24, 24, {tab});
                        const moreDescription = document.querySelector('.tab-action-description')?.textContent || '';
                        closeSessionContextMenu();
                        if (!moreDescription.includes('e2e-feature') || !moreDescription.includes('git-fixture')) {
                          throw new Error(`More Desc omitted metadata: ${moreDescription}`);
                        }
                        const initialStatus = await json(`/api/tmux-status?session=${encodeURIComponent(session)}`);
                        if (initialStatus.status !== 200) throw new Error(`tmux status GET ${initialStatus.status}`);
                        const statusCycle = [initialStatus.body.status];
                        for (let index = 0; index < 3; index += 1) {
                          const next = await json(`/api/tmux-status?session=${encodeURIComponent(session)}`, {method: 'POST'});
                          if (next.status !== 200) throw new Error(`tmux status POST ${next.status}`);
                          statusCycle.push(next.body.status);
                        }
                        if (new Set(statusCycle).size !== 3 || statusCycle[3] !== statusCycle[0]) {
                          throw new Error(`tmux status did not cycle top/bottom/none: ${JSON.stringify(statusCycle)}`);
                        }
                        const event = await json('/api/event', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({session, type: 'surface_probe', message: 'Docker event surface ready', details: {source: 'surface-e2e'}})});
                        if (event.status !== 200) throw new Error(`event POST ${event.status}`);
                        activateTab(session, 'events', {userInitiated: true});
                        await waitFor(() => document.querySelector(`#events-${CSS.escape(session)} .event-item`)?.textContent?.includes('Docker event surface ready'), 'Event Log');
                        activateTab(session, 'transcript', {userInitiated: true});
                        await waitFor(() => document.querySelector(`#transcript-${CSS.escape(session)} .transcript-item`)?.textContent?.includes('Docker transcript first'), 'Transcript');
                        done({graphVersion: graph.version, repositoryCount: repos.length, tabberActivityMs: tabberReloadDurationMs, tabberFirstPaintMs, tabberCacheHit: tabberActivityResponse.body.cache.hit, tabberSessionRow: true, infoBar: document.getElementById(`meta-${session}`)?.textContent || '', moreDescription, statusCycle, eventLoaded: true, transcriptLoaded: true, expectedPath});
                      } catch (error) {
                        done({error: String(error?.stack || error), body: document.body.innerText.slice(0, 1200), console: 'see browser log'});
                      }
                    })();
                """, session, str(git_root), tabber_reload_duration_ms, tabber_reload_first_paint_ms)
                if surfaces.get("error") or surfaces.get("eventLoaded") is not True or surfaces.get("transcriptLoaded") is not True:
                    surfaces["browser_log"] = driver.get_log("browser")
                    raise SystemExit(f"Go Chromium session surface E2E failed: {surfaces}")
                print(f"Go browser warm Tabber activity: {json.dumps({key: surfaces[key] for key in ('tabberActivityMs', 'tabberFirstPaintMs', 'tabberCacheHit', 'tabberSessionRow')}, sort_keys=True)}")
                normal_sessions = []
                for index in range(3):
                    name = f"normal-native-{os.getpid()}-{index}"
                    subprocess.run((*tmux, "new-session", "-d", "-s", name, "/bin/bash"), check=True)
                    created_sessions.append(name)
                    normal_sessions.append(name)
                existing_normal_sessions = set(subprocess.check_output((*tmux, "list-sessions", "-F", "#{session_name}"), text=True).splitlines())
                normal_multi_session = next(str(candidate) for candidate in range(1, 10) if str(candidate) not in existing_normal_sessions)
                subprocess.run((*tmux, "new-session", "-d", "-s", normal_multi_session, "-n", "codex", "/bin/bash"), check=True)
                created_sessions.append(normal_multi_session)
                subprocess.run((*tmux, "new-window", "-d", "-t", f"={normal_multi_session}:", "-n", "bash", "/bin/bash"), check=True)
                driver.get(f"https://127.0.0.1:{port}/")
                WebDriverWait(driver, 15).until(lambda browser: browser.execute_script("return typeof sessions !== 'undefined' && arguments[0].every(name => sessions.includes(name))", [*normal_sessions, normal_multi_session]))
                isolated_attention_journey(driver, tmux, normal_sessions)
                normal_file = finder_root / 'normal-editor.js'
                normal_file.write_text('const normal = true;\n', encoding='utf-8')
                normal_tabber_journey(driver, tmux, normal_sessions, normal_file)
                normal_multiwindow_journey(driver, tmux, normal_multi_session)
                driver.get(tabber_reload_url)
                WebDriverWait(driver, 15).until(lambda browser: browser.execute_script("return typeof apiFetchJson === 'function' && typeof terminals !== 'undefined'"))
                phase3 = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const session = arguments[0];
                    const events = new EventSource('/api/client-events?channels=core,events&client_id=phase3-e2e');
                    const seen = [];
                    let settled = false;
                    const finish = value => { if (settled) return; settled = true; events.close(); done(value); };
                    events.addEventListener('ready', () => {
                      seen.push('ready');
                      fetch('/api/event', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({session, type: 'note', message: 'phase3 browser event', details: {source: 'phase3-e2e'}}),
                      }).then(async response => {
                        const body = await response.text();
                        if (!response.ok) finish({error: 'event POST failed', status: response.status, body, seen});
                      }).catch(error => finish({error: 'event POST failed', detail: String(error), seen}));
                    });
                    events.addEventListener('event_log_changed', () => {
                      seen.push('published');
                      if (seen.includes('ready')) finish(seen);
                    });
                    setTimeout(() => finish({error: 'client-events timeout', seen}), 5000);
                """, session)
                if not isinstance(phase3, list) or 'ready' not in phase3 or 'published' not in phase3:
                    raise SystemExit(f"Go Phase 3 Chromium E2E failed: {phase3}")
                editor = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const sourcePath = arguments[0];
                    const markdownPath = arguments[1];
                    const sourceOriginal = arguments[2];
                    const markdownOriginal = arguments[3];
                    const waitFor = (predicate, description, timeoutMs = 12000) => new Promise((resolve, reject) => {
                      const deadline = performance.now() + timeoutMs;
                      const poll = () => {
                        try {
                          const value = predicate();
                          if (value) { resolve(value); return; }
                        } catch (error) { reject(error); return; }
                        if (performance.now() >= deadline) { reject(new Error(`${description} timed out`)); return; }
                        setTimeout(poll, 40);
                      };
                      poll();
                    });
                    const json = async (url, options = {}) => {
                      const response = await fetch(url, {cache: 'no-store', credentials: 'same-origin', ...options});
                      const body = await response.json();
                      return {status: response.status, body};
                    };
                    const operationPayload = async (url, label, options = {}) => {
                      const accepted = await json(url, options);
                      if (accepted.status === 200) return accepted.body?.state === 'ready' ? accepted.body.data : accepted.body;
                      if (accepted.status !== 202 || !accepted.body.operation?.id) throw new Error(`${label} was not accepted: ${JSON.stringify(accepted)}`);
                      for (let attempt = 0; attempt < 160; attempt += 1) {
                        const terminal = await json('/api/operations/' + encodeURIComponent(accepted.body.operation.id));
                        if (terminal.status === 202) {
                          await new Promise(resolve => setTimeout(resolve, 25));
                          continue;
                        }
                        if (terminal.status !== 200 || terminal.body.state !== 'ready') throw new Error(`${label} terminal: ${JSON.stringify(terminal)}`);
                        return terminal.body.data;
                      }
                      throw new Error(`${label} timed out`);
                    };
                    const panelFor = path => fileEditorPanelsForPath(path)[0];
                    const editorText = path => panelFor(path)?._cmView?.state?.doc?.toString?.() || '';
                    (async () => {
                      try {
                        const sourceItem = await openFileInEditor(sourcePath, {name: 'editor.go'}, {userInitiated: true, viewMode: 'edit'});
                        await waitFor(() => panelFor(sourcePath)?._cmView && editorText(sourcePath) === sourceOriginal, 'TextEdit open');
                        const sourcePanel = panelFor(sourcePath);
                        if (!sourcePanel?.querySelector('.cm-content')) throw new Error('TextEdit CodeMirror content is missing');
                        const sourceInfo = await json('/api/fs/info?path=' + encodeURIComponent(sourcePath));
                        if (sourceInfo.status !== 200 || sourceInfo.body.data?.kind !== 'file') throw new Error(`TextEdit info mismatch: ${JSON.stringify(sourceInfo)}`);
                        done({
                          sourceItem,
                          sourceText: editorText(sourcePath),
                          sourceMtime: sourceInfo.body.data.mtime_ns,
                          sourcePanel: true,
                          markdownOriginal,
                        });
                      } catch (error) {
                        done({error: String(error?.stack || error)});
                      }
                    })();
                """, str(source_file), str(markdown_file), source_original, markdown_original)
                if editor.get('error') or editor.get('sourceText') != source_original or editor.get('sourcePanel') is not True:
                    raise SystemExit(f"Go Phase 6 TextEdit open failed: {editor}")
                source_element = driver.find_element("css selector", f'.file-editor-panel[data-file-path="{source_file}"] .cm-content')
                source_element.click()
                driver.execute_script("fileEditorAutosaveEnabled = false;")
                native_suffix = "// native TextEdit input"
                for character in native_suffix:
                    source_element.send_keys(character)
                try:
                    WebDriverWait(driver, 12).until(
                        lambda current: current.execute_script(
                            """
                            const path = arguments[0];
                            const suffix = arguments[1];
                            const panel = fileEditorPanelsForPath(path)[0];
                            const text = panel?._cmView?.state?.doc?.toString?.() || '';
                            const state = fileState.get(path);
                            return text.includes(suffix) && state?.content === text && state?.dirty === true;
                            """,
                            str(source_file),
                            native_suffix,
                        )
                    )
                except TimeoutException as error:
                    diagnostics = driver.execute_script(
                        """
                        const path = arguments[0];
                        const panel = fileEditorPanelsForPath(path)[0];
                        const text = panel?._cmView?.state?.doc?.toString?.() || '';
                        const state = fileState.get(path);
                        return {text, stateContent: state?.content || '', dirty: state?.dirty === true, activeTag: document.activeElement?.tagName || '', activeClass: document.activeElement?.className || ''};
                        """,
                        str(source_file),
                    )
                    raise SystemExit(f"Go Phase 6 native TextEdit state propagation failed: {diagnostics}") from error
                text_after_input = driver.execute_script("return fileEditorPanelsForPath(arguments[0])[0]?._cmView?.state?.doc?.toString?.() || '';", str(source_file))
                expected_saved_text = text_after_input + "\n" if source_original.endswith("\n") and not text_after_input.endswith("\n") else text_after_input
                save_button = driver.find_element("css selector", f'.file-editor-panel[data-file-path="{source_file}"] .file-editor-save-panel')
                for close_button in driver.find_elements("css selector", "[data-toast-close]"):
                    if close_button.is_displayed():
                        close_button.click()
                save_selector = f'.file-editor-panel[data-file-path="{source_file}"] .file-editor-save-panel'
                try:
                    WebDriverWait(driver, 12).until(
                        lambda current: current.execute_script(
                            """
                            const button = document.querySelector(arguments[1]);
                            if (fileState.get(arguments[0])?.dirty !== true || button?.disabled) return false;
                            const rect = button?.getBoundingClientRect();
                            if (!rect || rect.width <= 0 || rect.height <= 0) return false;
                            const top = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
                            return top === button || button.contains(top);
                            """,
                            str(source_file),
                            save_selector,
                        )
                    )
                except TimeoutException as error:
                    diagnostics = driver.execute_script(
                        """
                        const button = document.querySelector(arguments[1]);
                        const rect = button?.getBoundingClientRect();
                        return {
                          dirty: fileState.get(arguments[0])?.dirty === true,
                          disabled: button?.disabled,
                          rect: rect?.toJSON?.() || null,
                          top: rect ? document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)?.outerHTML?.slice(0, 240) || '' : '',
                          toasts: [...document.querySelectorAll('.toast-header')].map(node => node.textContent?.trim() || ''),
                        };
                        """,
                        str(source_file),
                        save_selector,
                    )
                    raise SystemExit(f"Go Phase 6 save control never became interactable: {diagnostics}") from error
                save_button.click()
                save_result = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const path = arguments[0];
                    const expected = arguments[1];
                    let finished = false;
                    const finish = value => {
                      if (finished) return;
                      finished = true;
                      done(value);
                    };
                    const waitFor = () => {
                      const state = fileState.get(path);
                      const panel = fileEditorPanelsForPath(path)[0];
                      return state?.dirty === false && panel?._cmView?.state?.doc?.toString?.() === expected;
                    };
                    const poll = () => waitFor() ? finish({saved: true, text: fileEditorPanelsForPath(path)[0]._cmView.state.doc.toString()}) : setTimeout(poll, 40);
                    poll();
                    setTimeout(() => {
                      const state = fileState.get(path);
                      const button = document.querySelector('.file-editor-panel[data-file-path="' + CSS.escape(path) + '"] .file-editor-save-panel');
                      finish({
                        error: 'TextEdit save timeout',
                        text: fileEditorPanelsForPath(path)[0]?._cmView?.state?.doc?.toString?.() || '',
                        expected,
                        textMatchesExpected: fileEditorPanelsForPath(path)[0]?._cmView?.state?.doc?.toString?.() === expected,
                        panelCount: fileEditorPanelsForPath(path).length,
                        stateContent: state?.content || '',
                        original: state?.original || '',
                        dirty: state?.dirty === true,
                        saving: state?.saving === true,
                        buttonDisabled: button?.disabled === true,
                        status: document.querySelector('.file-editor-status')?.textContent?.trim() || '',
                      });
                    }, 12000);
                """, str(source_file), expected_saved_text)
                if save_result.get('error'):
                    raise SystemExit(f"Go Phase 6 TextEdit save failed: {save_result}")
                if source_file.read_text(encoding="utf-8") != expected_saved_text:
                    raise SystemExit("Go Phase 6 TextEdit disk content did not match the native editor input")
                local_conflict_suffix = "\n// local unsaved conflict"
                for character in local_conflict_suffix:
                    source_element.send_keys(character)
                try:
                    WebDriverWait(driver, 12).until(
                        lambda current: current.execute_script(
                            """
                            const path = arguments[0];
                            const expected = arguments[1];
                            const panel = fileEditorPanelsForPath(path)[0];
                            const text = panel?._cmView?.state?.doc?.toString?.() || '';
                            const state = fileState.get(path);
                            return text.endsWith(expected) && state?.content === text && state?.dirty === true;
                            """,
                            str(source_file),
                            local_conflict_suffix,
                        )
                    )
                except TimeoutException as error:
                    raise SystemExit("Go Phase 6 local unsaved conflict input did not reach file state") from error
                source_file.write_text("external replacement\n", encoding="utf-8")
                conflict = driver.execute_async_script(
                    """
                    const done = arguments[arguments.length - 1];
                    const path = arguments[0];
                    const panel = fileEditorPanelsForPath(path)[0];
                    let finished = false;
                    let externalAction = 'not-present';
                    const finish = value => {
                      if (finished) return;
                      finished = true;
                      done(value);
                    };
                    const deadline = performance.now() + 12000;
                    const observe = () => {
                      const externalDialog = document.querySelector('.file-editor-external-change-dialog');
                      if (externalDialog) {
                        const keepEditing = [...externalDialog.querySelectorAll('button')]
                          .find(button => /^keep editing$/i.test(button.textContent?.trim() || ''));
                        if (!keepEditing) {
                          finish({error: 'external-change dialog has no Keep editing action', text: externalDialog.textContent || ''});
                          return;
                        }
                        externalAction = 'keep-editing';
                        keepEditing.click();
                        setTimeout(observe, 0);
                        return;
                      }
                      const dialog = document.querySelector('.file-editor-conflict-dialog');
                      if (dialog) {
                        const buttons = [...dialog.querySelectorAll('button')];
                        const cancel = buttons.find(button => /^cancel$/i.test(button.textContent?.trim() || ''));
                        if (!cancel) {
                          finish({error: 'conflict dialog has no Cancel action', text: dialog.textContent || ''});
                          return;
                        }
                        const dialogText = dialog.textContent || '';
                        cancel.click();
                        savePromise.then(result => finish({dialog: true, cancelled: result === false, result, externalAction, text: dialogText, editor: panel?._cmView?.state?.doc?.toString?.() || '', dirty: fileState.get(path)?.dirty === true}), error => finish({error: String(error?.stack || error)}));
                        return;
                      }
                      if (performance.now() >= deadline) {
                        finish({error: 'TextEdit conflict dialog timeout', editor: panel?._cmView?.state?.doc?.toString?.() || '', dirty: fileState.get(path)?.dirty === true});
                        return;
                      }
                      setTimeout(observe, 40);
                    };
                    const savePromise = saveFileEditor(path, panel);
                    observe();
                    """,
                    str(source_file),
                )
                if conflict.get('error') or conflict.get('dialog') is not True or conflict.get('cancelled') is not True or conflict.get('dirty') is not True or local_conflict_suffix not in conflict.get('editor', ''):
                    raise SystemExit(f"Go Phase 6 expected-mtime conflict failed: {conflict}")
                markdown = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const path = arguments[0];
                    const expected = arguments[1];
                    (async () => {
                      try {
                        const item = await openFileInEditor(path, {name: 'editor.md'}, {userInitiated: true, viewMode: 'split'});
                        const panel = () => fileEditorPanelsForPath(path)[0];
                        const waitFor = (predicate, description, timeoutMs = 12000) => new Promise((resolve, reject) => {
                          const deadline = performance.now() + timeoutMs;
                          const poll = () => {
                            try {
                              const value = predicate();
                              if (value) { resolve(value); return; }
                            } catch (error) { reject(error); return; }
                            if (performance.now() >= deadline) { reject(new Error(`${description} timed out`)); return; }
                            setTimeout(poll, 40);
                          };
                          poll();
                        });
                        try {
                          await waitFor(() => panel()?._cmView && panel()?._pmView && panel()._cmView.state.doc.toString() === expected, 'ViewEdit open');
                        } catch (error) {
                          const currentPanel = panel();
                          const currentState = currentPanel ? fileEditorPanelState(currentPanel) : fileState.get(path);
                          done({
                            error: String(error?.stack || error),
                            phase: 'open',
                            panelCount: fileEditorPanelsForPath(path).length,
                            panelMode: currentPanel ? fileEditorPanelMode(currentPanel) : '',
                            panelPath: currentPanel?.dataset?.filePath || '',
                            state: currentState ? {
                              kind: currentState.kind || '',
                              loading: currentState.loading === true,
                              content: currentState.content || '',
                              original: currentState.original || '',
                              dirty: currentState.dirty === true,
                            } : null,
                            cm: currentPanel?._cmView?.state?.doc?.toString?.() || '',
                            pm: Boolean(currentPanel?._pmView),
                            pmError: currentPanel?._pmError || '',
                            pmEnsure: Boolean(currentPanel?._pmEnsurePromise),
                            pmState: currentPanel?.querySelector('.file-editor-preview-pane-panel')?.dataset?.prosemirrorState || '',
                            previewText: currentPanel?.querySelector('.file-editor-preview-pane-panel')?.textContent || '',
                            status: currentPanel?.querySelector('.file-editor-status')?.textContent?.trim() || '',
                            scripts: [...document.scripts].map(script => script.src).filter(src => src.includes('prosemirror')),
                          });
                          return;
                        }
                        const view = panel()._pmView;
                        let paragraphStart = 1;
                        let paragraph = null;
                        view.state.doc.forEach((node, offset) => {
                          if (!paragraph && node.type.name === 'paragraph' && node.textContent.includes('A paragraph before')) {
                            paragraph = node;
                            paragraphStart = offset + 1;
                          }
                        });
                        if (!paragraph) throw new Error('ViewEdit paragraph selection target is missing');
                        view.dispatch(view.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(view.state.doc, paragraphStart + paragraph.content.size)));
                        view.focus();
                        const png = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='), byte => byte.charCodeAt(0));
                        const transfer = new DataTransfer();
                        transfer.items.add(new File([png], 'screen.png', {type: 'image/png'}));
                        const event = new ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: transfer});
                        document.dispatchEvent(event);
                        const imageFor = () => panel()._pmView?.dom?.querySelector('img.markdown-preview-image');
                        await waitFor(() => imageFor(), 'ViewEdit upload image');
                        try {
                          await waitFor(() => imageFor()?.naturalWidth > 0, 'ViewEdit uploaded image decode');
                        } catch (error) {
                          const rawRequests = [...performance.getEntriesByType('resource')]
                            .map(entry => entry.name)
                            .filter(name => name.includes('/api/fs/raw'));
                          const images = [...panel()._pmView.dom.querySelectorAll('img')].map(node => ({outerHTML: node.outerHTML, src: node.src, resolvedPath: node.dataset.resolvedPath || '', naturalWidth: node.naturalWidth, naturalHeight: node.naturalHeight, complete: node.complete}));
                          const image = imageFor();
                          throw new Error(`${error.message}; image=${JSON.stringify({src: image?.src || '', resolvedPath: image?.dataset.resolvedPath || '', naturalWidth: image?.naturalWidth || 0, naturalHeight: image?.naturalHeight || 0, complete: image?.complete === true})}; images=${JSON.stringify(images)}; rawRequests=${JSON.stringify(rawRequests)}; source=${JSON.stringify(fileEditorPanelState(panel()).content)}`);
                        }
                        const image = imageFor();
                        const imageReferenceFor = value => String(value || '').split(String.fromCharCode(10)).find(line => line.includes('![') && line.includes('](editor/')) || '';
                        try {
                          await waitFor(() => imageReferenceFor(fileEditorPanelState(panel()).content), 'ViewEdit source upload synchronization');
                        } catch (error) {
                          const currentPanel = panel();
                          const currentState = fileEditorPanelState(currentPanel);
                          done({
                            error: String(error?.stack || error),
                            afterImage: {
                              source: currentState?.content || '',
                              dirty: currentState?.dirty === true,
                              pmText: currentPanel?._pmView?.state?.doc?.textContent || '',
                              pmJson: currentPanel?._pmView?.state?.doc?.toJSON?.() || null,
                              serialized: serializeProseMirrorSource(currentPanel),
                              pmSource: currentPanel?._pmSource || null,
                              pmSerializeTimer: Boolean(currentPanel?._pmSerializeTimer),
                              selection: currentPanel?._pmView?.state?.selection ? {
                                from: currentPanel._pmView.state.selection.from,
                                to: currentPanel._pmView.state.selection.to,
                              } : null,
                              cm: currentPanel?._cmView?.state?.doc?.toString?.() || '',
                            },
                          });
                          return;
                        }
                        const source = fileEditorPanelState(panel()).content;
                        const sourceImageReference = imageReferenceFor(source);
                        await waitFor(() => panel()._cmView.state.doc.toString() === source, 'ViewEdit source synchronization');
                        const serialized = serializeProseMirrorSource(panel());
                        const canonicalSerialized = source.endsWith('\\n') && !serialized.endsWith('\\n') ? `${serialized}\\n` : serialized;
                        done({item, prevented: event.defaultPrevented, source, sourceImageReference, serialized: canonicalSerialized, imageSource: image.dataset.resolvedPath || '', imageReady: image.naturalWidth > 0, cm: panel()._cmView.state.doc.toString()});
                      } catch (error) {
                        done({error: String(error?.stack || error)});
                      }
                    })();
                """, str(markdown_file), markdown_original)
                if markdown.get('error') or markdown.get('prevented') is not True or markdown.get('imageReady') is not True or not markdown.get('sourceImageReference') or not markdown.get('imageSource') or markdown.get('cm') != markdown.get('source') or markdown.get('serialized') != markdown.get('source'):
                    raise SystemExit(f"Go Phase 7 ViewEdit upload/round-trip failed: {markdown}")
                markdown_saved_source = markdown['source'] + "\n" if markdown_original.endswith("\n") and not markdown['source'].endswith("\n") else markdown['source']
                markdown_save = driver.find_element("css selector", f'.file-editor-panel[data-file-path="{markdown_file}"] .file-editor-save-panel')
                try:
                    WebDriverWait(driver, 12).until(
                        lambda current: current.execute_script(
                            """
                            const button = document.querySelector(arguments[0]);
                            const path = arguments[1];
                            return button && !button.disabled && fileState.get(path)?.dirty === true;
                            """,
                            f'.file-editor-panel[data-file-path="{markdown_file}"] .file-editor-save-panel',
                            str(markdown_file),
                        )
                    )
                except TimeoutException as error:
                    raise SystemExit("Go Phase 7 save control never became enabled") from error
                try:
                    markdown_save.click()
                except Exception as error:
                    save_click_diagnostics = driver.execute_script(
                        """
                        const path = arguments[0];
                        const state = fileState.get(path);
                        const panel = fileEditorPanelsForPath(path)[0];
                        return {
                          error: String(arguments[1]),
                          modal: document.querySelector('.file-editor-external-change-dialog')?.textContent?.trim() || document.querySelector('.file-editor-conflict-dialog')?.textContent?.trim() || '',
                          state: state ? {content: state.content || '', original: state.original || '', dirty: state.dirty === true, externalChanged: state.externalChanged || null, externalMissing: state.externalMissing === true, externalError: state.externalError || ''} : null,
                          panelMode: panel ? fileEditorPanelMode(panel) : '',
                          pmSource: panel?._pmSource || null,
                          serialized: panel?._pmView ? serializeProseMirrorSource(panel) : null,
                        };
                        """,
                        str(markdown_file),
                        str(error),
                    )
                    raise SystemExit(f"Go Phase 7 Markdown save click was intercepted: {save_click_diagnostics}") from error
                markdown_save_result = driver.execute_async_script(
                    """
                    const done = arguments[arguments.length - 1];
                    const path = arguments[0];
                    const expected = arguments[1];
                    const deadline = performance.now() + 12000;
                    const poll = () => {
                      const panel = fileEditorPanelsForPath(path)[0];
                      const state = fileState.get(path);
                      const text = panel?._cmView?.state?.doc?.toString?.() || '';
                      if (state?.dirty === false && text === expected) {
                        done({saved: true, text});
                        return;
                      }
                      if (performance.now() >= deadline) {
                        done({error: 'ViewEdit save timeout', text, expected, textMatchesExpected: text === expected, dirty: state?.dirty === true});
                        return;
                      }
                      setTimeout(poll, 40);
                    };
                    poll();
                    """,
                    str(markdown_file),
                    markdown_saved_source,
                )
                if markdown_save_result.get('error') or markdown_save_result.get('saved') is not True:
                    raise SystemExit(f"Go Phase 7 Markdown save failed: {markdown_save_result}")
                if markdown_file.read_text(encoding="utf-8") != markdown_saved_source:
                    raise SystemExit("Go Phase 7 Markdown disk content did not match serialized ViewEdit source")
                markdown_reopened = driver.execute_async_script(
                    """
                    const done = arguments[arguments.length - 1];
                    const path = arguments[0];
                    const expected = arguments[1];
                    (async () => {
                      try {
                        await reloadOpenFileFromDisk(path, {force: true});
                        const deadline = performance.now() + 12000;
                        const poll = () => {
                          const panel = fileEditorPanelsForPath(path)[0];
                          const cm = panel?._cmView?.state?.doc?.toString?.() || '';
                          const pm = panel?._pmView;
                          const serialized = pm ? serializeProseMirrorSource(panel) : '';
                          const canonicalSerialized = expected.endsWith('\\n') && !serialized.endsWith('\\n') ? `${serialized}\\n` : serialized;
                          if (cm === expected && pm?.dom?.isConnected && canonicalSerialized === expected) {
                            done({reopened: true, cm, serialized: canonicalSerialized, pmReady: true});
                            return;
                          }
                          if (performance.now() >= deadline) {
                            done({error: 'ViewEdit reopen timeout', cm, serialized: canonicalSerialized, pmReady: Boolean(pm?.dom?.isConnected)});
                            return;
                          }
                          setTimeout(poll, 40);
                        };
                        poll();
                      } catch (error) {
                        done({error: String(error?.stack || error)});
                      }
                    })();
                    """,
                    str(markdown_file),
                    markdown_saved_source,
                )
                if markdown_reopened.get('error') or markdown_reopened.get('reopened') is not True or markdown_reopened.get('serialized') != markdown_saved_source:
                    raise SystemExit(f"Go Phase 7 Markdown reopen failed: {markdown_reopened}")
                unsupported = driver.execute_async_script(
                    """
                    const done = arguments[arguments.length - 1];
                    const path = arguments[0];
                    (async () => {
                      try {
                        await openFileInEditor(path, {name: 'unsupported.md'}, {userInitiated: true, viewMode: 'preview'});
                        const deadline = performance.now() + 12000;
                        const poll = () => {
                          const panel = fileEditorPanelsForPath(path)[0];
                          const error = panel?._pmError || '';
                          const watermark = panel?.querySelector('.file-editor-prosemirror-error');
                          if (error && watermark) {
                            done({failedClosed: true, error, watermark: watermark.textContent || '', status: panel.querySelector('.file-editor-status')?.textContent?.trim() || ''});
                            return;
                          }
                          if (performance.now() >= deadline) {
                            done({error: 'unsupported ViewEdit failure timeout', pm: Boolean(panel?._pmView), panelError: error, watermark: Boolean(watermark)});
                            return;
                          }
                          setTimeout(poll, 40);
                        };
                        poll();
                      } catch (error) {
                        done({error: String(error?.stack || error)});
                      }
                    })();
                    """,
                    str(unsupported_markdown_file),
                )
                if unsupported.get('failedClosed') is not True or unsupported.get('pm') is True or 'unsupported HTML tag' not in unsupported.get('error', ''):
                    raise SystemExit(f"Go Phase 7 unsupported ViewEdit contract failed: {unsupported}")
                finder = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const root = arguments[0];
                    const note = root + '/note.md';
                    const json = async (path, options = {}) => {
                      const response = await fetch(path, {cache: 'no-store', credentials: 'same-origin', ...options});
                      const body = await response.json();
                      return {status: response.status, body};
                    };
                    const requireStatus = (result, status, label) => {
                      if (result.status !== status) throw new Error(`${label}: ${result.status} ${JSON.stringify(result.body)}`);
                      if (result.body?.state === 'ready') return result.body.data;
                      return result.body;
                    };
                    const operationPayload = async (url, label, options = {}) => {
                      const accepted = await json(url, options);
                      if (accepted.status === 200) return accepted.body?.state === 'ready' ? accepted.body.data : accepted.body;
                      if (accepted.status !== 202 || !accepted.body.operation?.id) throw new Error(`${label} was not accepted: ${JSON.stringify(accepted)}`);
                      for (let attempt = 0; attempt < 160; attempt += 1) {
                        const terminal = await json('/api/operations/' + encodeURIComponent(accepted.body.operation.id));
                        if (terminal.status === 202) {
                          await new Promise(resolve => setTimeout(resolve, 25));
                          continue;
                        }
                        if (terminal.status !== 200 || terminal.body.state !== 'ready') throw new Error(`${label} terminal: ${JSON.stringify(terminal)}`);
                        return terminal.body.data;
                      }
                      throw new Error(`${label} timed out`);
                    };
                    (async () => {
                      const listing = await operationPayload('/api/fs/list?path=' + encodeURIComponent(root), 'list');
                      if (!listing.entries.some(entry => entry.name === 'nested' && entry.kind === 'dir')) throw new Error('list missed nested directory');
                      const read = requireStatus(await json('/api/fs/read?path=' + encodeURIComponent(note)), 200, 'read');
                      if (read.content !== 'hello from finder\\n') throw new Error('read content mismatch');
                      const info = requireStatus(await json('/api/fs/info?path=' + encodeURIComponent(note)), 200, 'info');
                      if (info.kind !== 'file') throw new Error('info kind mismatch');
                      const rawResponse = await fetch('/api/fs/raw?path=' + encodeURIComponent(note), {cache: 'no-store', credentials: 'same-origin', headers: {Range: 'bytes=0-4'}});
                      if (rawResponse.status !== 206 || await rawResponse.text() !== 'hello') throw new Error('raw range mismatch');
                      const batch = await operationPayload('/api/fs/batch', 'batch', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({requests: [{id: 'list', type: 'list', path: root}, {id: 'info', type: 'info', path: note}]})});
                      if (batch.responses.length !== 2 || !batch.responses.every(response => response.ok)) throw new Error('batch response mismatch');
                      const candidate = requireStatus(await json('/api/fs/resolve-file-candidates', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({paths: [root + '/missing.txt', note]})}), 200, 'candidates');
                      if (candidate.path !== note || candidate.misses.length !== 1) throw new Error('candidate resolution mismatch');
                      const created = root + '/created.txt';
                      await operationPayload('/api/fs/write', 'write', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path: created, content: 'created'})});
                      await operationPayload('/api/fs/rename', 'rename', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path: created, new_name: 'renamed.txt'})});
                      await operationPayload('/api/fs/delete', 'delete', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path: root + '/renamed.txt'})});
                      const emptyDir = root + '/empty-dir';
                      await operationPayload('/api/fs/mkdir', 'mkdir', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path: emptyDir})});
                      const deletedDir = await operationPayload('/api/fs/delete', 'delete directory', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path: emptyDir})});
                      if (deletedDir.deleted !== true || deletedDir.kind !== 'dir') throw new Error('directory delete mismatch');
                      const count = await operationPayload('/api/batch/count?path=' + encodeURIComponent(root), 'count');
                      if (count.files < 3 || count.recursive !== true) throw new Error('count mismatch: ' + JSON.stringify(count));
                      const zipResponse = await fetch('/api/batch/zip?path=' + encodeURIComponent(root), {cache: 'no-store', credentials: 'same-origin'});
                      const zipBytes = await zipResponse.arrayBuffer();
                      if (zipResponse.status !== 200 || zipBytes.byteLength < 22 || !zipResponse.headers.get('Content-Disposition')?.includes('.zip')) throw new Error('zip mismatch: ' + zipResponse.status + ' ' + zipBytes.byteLength);
                      const htmlResponse = await fetch('/api/batch/html-preview?path=' + encodeURIComponent(root + '/index.html'), {cache: 'no-store', credentials: 'same-origin'});
                      const htmlBody = await htmlResponse.text();
                      if (htmlResponse.status !== 200 || !htmlBody.includes('sandbox="allow-scripts allow-forms allow-popups"') || !htmlBody.includes('&lt;script&gt;')) throw new Error('html preview mismatch: ' + htmlResponse.status);
                      const streamResponse = await fetch('/api/fs/search-stream?root=' + encodeURIComponent(root) + '&query=needle&limit=10', {cache: 'no-store', credentials: 'same-origin', headers: {Accept: 'text/event-stream'}});
                      const streamBody = await streamResponse.text();
                      if (streamResponse.status !== 200 || !streamBody.includes('event: chunk') || !streamBody.includes('needle.txt')) throw new Error('search stream mismatch');
                      const accepted = await json('/api/fs/search?root=' + encodeURIComponent(root) + '&query=needle&recursive=1&limit=10');
                      if (accepted.status !== 202 || !accepted.body.operation?.id) throw new Error('recursive search was not accepted');
                      let terminal = null;
                      for (let attempt = 0; attempt < 80; attempt += 1) {
                        const operation = await json('/api/operations/' + encodeURIComponent(accepted.body.operation.id));
                        if (operation.status !== 202) { terminal = operation; break; }
                        await new Promise(resolve => setTimeout(resolve, 25));
                      }
                      if (!terminal || terminal.status !== 200 || terminal.body.state !== 'ready' || terminal.body.data.count !== 1) throw new Error('recursive search terminal mismatch: ' + JSON.stringify(terminal));
                      const uploadForm = new FormData();
                      uploadForm.append('files', new Blob(['uploaded'], {type: 'text/plain'}), 'asset.txt');
                      const upload = await operationPayload('/api/upload?editor_path=' + encodeURIComponent(note), 'upload', {method: 'POST', body: uploadForm});
                      if (upload.target_source !== 'editor_file_directory' || upload.files?.length !== 1) throw new Error('upload mismatch: ' + JSON.stringify(upload));
                      const external = await json('/api/fs/read?path=%2Fetc%2Fpasswd');
                      if (external.status !== 200 || !external.body.content?.includes('root:')) throw new Error('external path access mismatch');
                      const forbidden = await json('/api/fs/read?path=' + encodeURIComponent(arguments[1]));
                      if (forbidden.status !== 403) throw new Error('permission failure mismatch: ' + forbidden.status);
                      const watch = requireStatus(await json('/api/watch/roots', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({client_id: 'finder-e2e', roots: [root]})}), 200, 'watch roots');
                      done({listing: listing.entries.length, read: read.content, infoKind: info.kind, batch: batch.responses.length, candidate: candidate.path, count: count.files, zipBytes: zipBytes.byteLength, htmlPreview: true, stream: true, recursiveSearch: terminal.body.data.count, upload: upload.files[0].relative_path, watchToken: watch.watch_token});
                    })().catch(error => done({error: String(error)}));
                """, str(finder_root), str(unreadable_file))
                if finder.get('error'):
                    raise SystemExit(f"Go Phase 4/5 Finder Chromium E2E failed: {finder}")
                driver.get(f"https://127.0.0.1:{port}/?sessions=files,{session}&layout=left&tabs=left:files")
                ui_deadline = time.time() + 15
                while time.time() < ui_deadline and not driver.find_elements("css selector", "#panel-__finder__ .file-explorer-tree-panel"):
                    time.sleep(0.1)
                finder_setup = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const root = arguments[0];
                    const gitRoot = arguments[1];
                    const waitFor = (predicate, description, timeoutMs = 12000) => new Promise((resolve, reject) => {
                      const deadline = performance.now() + timeoutMs;
                      const poll = () => {
                        try {
                          const value = predicate();
                          if (value) { resolve(value); return; }
                        } catch (error) { reject(error); return; }
                        if (performance.now() >= deadline) { reject(new Error('Finder setup condition timed out')); return; }
                        setTimeout(poll, 50);
                      };
                      poll();
                    });
                    const row = path => document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${CSS.escape(path)}"]`);
                    (async () => {
                      if (typeof openFileExplorerAt !== 'function') throw new Error('Finder UI opener is unavailable');
                      await waitFor(() => typeof tabberIsAdvanced === 'function' && document.querySelector('[data-tabber-advanced]'), 'Tabber toolbar');
                      if (!tabberIsAdvanced()) {
                        document.querySelector('[data-tabber-advanced]').click();
                        await waitFor(() => tabberIsAdvanced(), 'Advanced mode');
                      }
                      await selectSession('__finder__', {userInitiated: true});
                      await waitFor(() => document.querySelector('#panel-__finder__ .file-explorer-tree-panel'), 'Finder panel');
                      const opened = await openFileExplorerAt(root, {user: true, manualSelection: true});
                      if (opened !== true) throw new Error(`Finder UI did not open: ${opened}`);
                      await waitFor(() => row(root + '/nested') && row(root + '/note.md'));
                      const initial = {
                        panel: Boolean(document.querySelector('#panel-__finder__')),
                        nested: Boolean(row(root + '/nested')),
                        note: Boolean(row(root + '/note.md')),
                        expanded: row(root + '/nested')?.getAttribute('aria-expanded') || null,
                      };
                      row(root + '/nested').click();
                      await waitFor(() => row(root + '/nested')?.getAttribute('aria-expanded') === 'true' && row(root + '/nested/needle.txt'));
                      const expanded = {
                        expanded: row(root + '/nested')?.getAttribute('aria-expanded') || null,
                        needle: Boolean(row(root + '/nested/needle.txt')),
                      };
                      row(root + '/nested').click();
                      await waitFor(() => row(root + '/nested')?.getAttribute('aria-expanded') === 'false' && !row(root + '/nested/needle.txt'));
                      row(root + '/nested').click();
                      await waitFor(() => row(root + '/nested')?.getAttribute('aria-expanded') === 'true' && row(root + '/nested/needle.txt'));
                      const reexpanded = {
                        expanded: row(root + '/nested')?.getAttribute('aria-expanded') || null,
                        needle: Boolean(row(root + '/nested/needle.txt')),
                      };
                      const gitRow = row(gitRoot);
                      if (!gitRow) throw new Error('Finder Git repository row is missing during setup');
                      const initialRepo = gitRow.dataset.isRepo === 'true';
                      try {
                        await waitFor(() => row(gitRoot)?.dataset.isRepo === 'true');
                      } catch (error) {
                        throw new Error(`Finder Git repository enrichment timed out: ${JSON.stringify({initialRepo, row: row(gitRoot)?.outerHTML || '', pending: [...fileExplorerRepoInfoEnrichmentState.pending], inFlight: [...fileExplorerRepoInfoEnrichmentState.inFlight], resolved: [...fileExplorerRepoInfoEnrichmentState.resolved]})}`, {cause: error});
                      }
                      window.__yomuxFinderContextMenuProbe = {defaultPrevented: false};
                      gitRow.addEventListener('contextmenu', event => {
                        window.__yomuxFinderContextMenuProbe.defaultPrevented = event.defaultPrevented;
                      }, {once: true});
                      done({initial, expanded, reexpanded, initialRepo, repoEnriched: row(gitRoot)?.dataset.isRepo === 'true'});
                    })().catch(error => done({error: String(error?.stack || error)}));
                """, str(finder_root), str(git_root))
                if finder_setup.get('error'):
                    raise SystemExit(f"Go Phase 4/5 Finder setup Chromium E2E failed: {finder_setup}")
                git_row = driver.find_element("css selector", f'#panel-__finder__ .file-tree-row[data-path="{git_root}"]')
                ActionChains(driver).context_click(git_row).perform()
                finder_ui = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const root = arguments[0];
                    const gitRoot = arguments[1];
                    const gitHead = arguments[2];
                    const setup = arguments[3];
                    const waitFor = (predicate, description, timeoutMs = 12000) => new Promise((resolve, reject) => {
                      let settled = false;
                      const controller = new AbortController();
                      const timeout = setTimeout(() => {
                        settled = true;
                        controller.abort();
                        reject(new Error(`Finder UI condition timed out: ${description}`));
                      }, timeoutMs);
                      const finish = (callback, value) => {
                        if (settled) return;
                        settled = true;
                        clearTimeout(timeout);
                        callback(value);
                      };
                      const poll = async () => {
                        if (settled) return;
                        try {
                          const value = await predicate(controller.signal);
                          if (value) { finish(resolve, value); return; }
                        } catch (error) { finish(reject, error); return; }
                        if (settled) return;
                        setTimeout(poll, 50);
                      };
                      poll();
                    });
                    const row = path => document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${CSS.escape(path)}"]`);
                    (async () => {
                      const gitRow = row(gitRoot);
                      if (!gitRow) throw new Error('Finder Git repository row is missing');
                      const gitDiffButton = await waitFor(() => Array.from(document.querySelectorAll('.file-context-menu button')).find(button => button.textContent.trim() === 'ΔShow Diff'), 'context-menu Diff action');
                      if (!gitDiffButton || gitDiffButton.disabled) throw new Error('Finder Git repository Diff action is missing or disabled');
                      const originalFetch = window.fetch.bind(window);
                      let heldStatsResponse = false;
                      window.fetch = async (input, init) => {
                        const response = await originalFetch(input, init);
                        const url = new URL(typeof input === 'string' ? input : input.url, location.href);
                        if (url.pathname !== '/api/fs/git-history-stats-stream' || heldStatsResponse || !response.body) return response;
                        heldStatsResponse = true;
                        window.__yomuxGoStatsHeldRequestUrl = url.href;
                        const sourceReader = response.body.getReader();
                        let eventTail = '';
                        let statsStreamText = '';
                        const heldBody = new ReadableStream({
                          start(controller) {
                            (async () => {
                              const decoder = new TextDecoder();
                              while (true) {
                                const result = await sourceReader.read();
                                if (result.done) { controller.close(); return; }
                                const text = decoder.decode(result.value, {stream: true});
                                statsStreamText = (statsStreamText + text).slice(-1000);
                                eventTail += text;
                                window.__yomuxGoStatsStreamText = statsStreamText;
                                controller.enqueue(result.value);
                                if (eventTail.includes('event: done')) {
                                  window.__yomuxGoStatsDoneHeldOpen = true;
                                  return;
                                }
                                eventTail = eventTail.slice(-128);
                              }
                            })().catch(error => controller.error(error));
                          },
                          cancel(reason) { return sourceReader.cancel(reason); },
                        });
                        return new Response(heldBody, {status: response.status, statusText: response.statusText, headers: response.headers});
                      };
                      const historyListStartedAt = performance.now();
                      gitDiffButton.click();
                      const gitDiffItem = gitDiffItemFor(gitRoot);
                      let gitDiffPanel;
                      try {
                        gitDiffPanel = await waitFor(() => {
                          const panel = panelNodes.get(gitDiffItem);
                           const rows = panel?.querySelectorAll('.git-diff-commit-row[data-git-diff-commit]') || [];
                           return panel && rows.length >= 2 ? panel : null;
                        }, 'Git history rows');
                      } catch (error) {
                        const historyState = gitDiffTabState.get(gitDiffItem);
                        throw new Error(`${error.message}: ${JSON.stringify({
                          state: historyState ? {
                            path: historyState.path,
                            loading: historyState.loading === true,
                            loadAttempted: historyState.loadAttempted === true,
                            loaded: historyState.loaded === true,
                            error: historyState.error,
                            commits: historyState.commits?.length || 0,
                          } : null,
                          panel: panelNodes.get(gitDiffItem)?.outerHTML?.slice(0, 2000) || '',
                          gitHistoryRequests: performance.getEntriesByType('resource').map(entry => entry.name).filter(name => name.includes('/api/fs/git-history')),
                        })}`, {cause: error});
                      }
                      const historyListReadyMs = performance.now() - historyListStartedAt;
                      const gitRows = Array.from(gitDiffPanel.querySelectorAll('.git-diff-commit-row'));
                      const shaRows = gitRows.filter(row => row.dataset.gitDiffCommit);
                      const topCommit = shaRows[0];
                      let topStats = '';
                      try {
                        topStats = await waitFor(() => {
                          const changes = topCommit?.querySelector('.git-diff-commit-changes');
                          const label = String(changes?.getAttribute('aria-label') || '');
                          return label && !changes.hasAttribute('aria-hidden') ? label : null;
                        }, 'top Git commit stats');
                      } catch (error) {
                        const historyState = gitDiffTabState.get(gitDiffItem);
                        throw new Error(`${error.message}: ${JSON.stringify({
                          state: historyState ? {
                            loaded: historyState.loaded,
                            loading: historyState.loading,
                            statsLoading: historyState.statsLoading,
                            statsStage: historyState.statsStage,
                            commits: historyState.commits?.slice(0, 3).map(commit => ({sha: commit.sha, files: commit.files, added: commit.added, removed: commit.removed, binary_files: commit.binary_files})),
                          } : null,
                          topRow: topCommit?.outerHTML || '',
                          statsRequests: performance.getEntriesByType('resource').map(entry => entry.name).filter(name => name.includes('/api/fs/git-history-stats-stream')),
                        })}`, {cause: error});
                      }
                      let doneCompletion;
                      try {
                        doneCompletion = await waitFor(() => {
                          const historyState = gitDiffTabState.get(gitDiffItem);
                          return window.__yomuxGoStatsDoneHeldOpen === true && historyState?.statsStage >= 1 && !historyState.statsLoading
                            ? {statsStage: historyState.statsStage, statsLoading: historyState.statsLoading}
                            : null;
                        }, 'Git stats done event completion while response stays open');
                      } catch (error) {
                        const historyState = gitDiffTabState.get(gitDiffItem);
                        throw new Error(`${error.message}: ${JSON.stringify({
                          heldStatsResponse,
                          doneEventResponseHeldOpen: window.__yomuxGoStatsDoneHeldOpen === true,
                          statsStreamText: window.__yomuxGoStatsStreamText || '',
                          state: historyState ? {statsLoading: historyState.statsLoading, statsStage: historyState.statsStage, statsError: historyState.statsError, commits: historyState.commits?.slice(0, 2)} : null,
                          statsRequests: performance.getEntriesByType('resource').map(entry => entry.name).filter(name => name.includes('/api/fs/git-history-stats-stream')),
                        })}`, {cause: error});
                      }
                      window.fetch = originalFetch;
                      const statsRequests = performance.getEntriesByType('resource')
                        .map(entry => entry.name)
                        .filter(name => name.includes('/api/fs/git-history-stats-stream'));
                      const historyRequests = performance.getEntriesByType('resource')
                        .map(entry => entry.name)
                        .filter(name => name.includes('/api/fs/git-history?'));
                      const historyRequest = historyRequests.length ? new URL(historyRequests[0], location.href) : null;
                      const historyRequestEntry = historyRequests.length
                        ? performance.getEntriesByName(historyRequests[0]).at(-1)
                        : null;
                      const heldStatsRequestUrl = window.__yomuxGoStatsHeldRequestUrl || '';
                      const heldStatsRequest = heldStatsRequestUrl ? new URL(heldStatsRequestUrl, location.href) : null;
                      topCommit.click();
                      const topDetail = await waitFor(() => {
                        const detail = gitDiffTabState.get(gitDiffItem)?.commitRecords?.get(gitHead)?.detail;
                        return detail?.sha === gitHead && Array.isArray(detail.files) ? detail : null;
                      }, 'loaded top Git commit detail');
                      const renderedTopDetail = gitDiffPanel.querySelector('.git-diff-commit-detail .git-diff-file-tree');
                      const gitHistory = {
                        nativeSuppressed: Boolean(window.__yomuxFinderContextMenuProbe?.defaultPrevented),
                        item: gitDiffItem,
                        active: itemIsActivePaneTab(gitDiffItem),
                        head: shaRows[0]?.dataset.gitDiffCommit || '',
                        commits: shaRows.length,
                        historyListReadyMs,
                        historyRequestMs: historyRequestEntry?.duration ?? null,
                        topStats,
                        doneCompletion,
                        doneEventResponseHeldOpen: window.__yomuxGoStatsDoneHeldOpen === true,
                        heldStatsBatchSizes: heldStatsRequest?.searchParams.get('batch_sizes') || '',
                        detailFiles: topDetail.files.map(file => file.path),
                        detailRendered: Boolean(renderedTopDetail),
                      };
                      if (!gitHistory.nativeSuppressed || !gitHistory.active || gitHistory.head !== gitHead || gitHistory.commits < 2 || gitHistory.historyListReadyMs >= 500 || !historyRequest || !gitHistory.topStats || !gitHistory.doneEventResponseHeldOpen || gitHistory.doneCompletion?.statsStage < 1 || gitHistory.doneCompletion?.statsLoading !== false || gitHistory.heldStatsBatchSizes !== '1' || !gitHistory.detailFiles.length || !gitHistory.detailRendered) throw new Error('Finder Git repository Diff tab contract mismatch: ' + JSON.stringify(gitHistory));
                      const removable = row(root + '/ui-remove-me.txt');
                      if (!removable) throw new Error('Finder delete fixture row is missing');
                      const originalConfirm = window.confirm;
                      window.confirm = () => true;
                      removable.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 32, clientY: 32}));
                      await waitFor(() => Array.from(document.querySelectorAll('.file-context-menu button')).find(button => /^delete$/i.test(button.textContent.trim())), 'context-menu Delete action');
                      const deleteButton = Array.from(document.querySelectorAll('.file-context-menu button')).find(button => /^delete$/i.test(button.textContent.trim()));
                      if (!deleteButton) throw new Error('Finder delete context-menu action is missing');
                      deleteButton.click();
                      await waitFor(() => !row(root + '/ui-remove-me.txt'), 'Finder delete convergence');
                      window.confirm = originalConfirm;
                      const deletedInfoStatus = await waitFor(async signal => {
                        const response = await fetch('/api/fs/info?path=' + encodeURIComponent(root + '/ui-remove-me.txt'), {cache: 'no-store', credentials: 'same-origin', signal});
                        if (response.status === 404) return response.status;
                        if (response.status !== 200) throw new Error(`Finder delete info returned unexpected status ${response.status}`);
                        return null;
                      }, 'Finder delete filesystem convergence');
                      if (deletedInfoStatus !== 404) throw new Error(`Finder delete did not remove the file: ${deletedInfoStatus}`);
                      done({initial: setup.initial, expanded: setup.expanded, reexpanded: setup.reexpanded, initialRepo: setup.initialRepo, repoEnriched: setup.repoEnriched, gitHistory, deletedViaFinder: true});
                    })().catch(error => done({error: String(error?.stack || error)}));
                """, str(finder_root), str(git_root), git_head, finder_setup)
                if finder_ui.get('error') or finder_ui.get('initial', {}).get('panel') is not True or finder_ui.get('expanded', {}).get('needle') is not True or finder_ui.get('reexpanded', {}).get('needle') is not True or finder_ui.get('repoEnriched') is not True or finder_ui.get('gitHistory', {}).get('nativeSuppressed') is not True or finder_ui.get('gitHistory', {}).get('active') is not True or finder_ui.get('gitHistory', {}).get('head') != git_head or finder_ui.get('gitHistory', {}).get('commits', 0) < 2 or finder_ui.get('gitHistory', {}).get('historyListReadyMs', 500) >= 500 or not finder_ui.get('gitHistory', {}).get('topStats') or finder_ui.get('gitHistory', {}).get('heldStatsBatchSizes') != '1' or not finder_ui.get('gitHistory', {}).get('detailFiles') or finder_ui.get('gitHistory', {}).get('detailRendered') is not True or finder_ui.get('deletedViaFinder') is not True:
                    raise SystemExit(f"Go Phase 4/5 Finder UI Chromium E2E failed: {finder_ui}")
                print(f"Go browser Git history readiness: {json.dumps(finder_ui['gitHistory'], sort_keys=True)}")
                git_views = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const path = arguments[0];
                    const repo = arguments[1];
                    const head = arguments[2];
                    const waitFor = (predicate, description, timeoutMs = 12000) => new Promise((resolve, reject) => {
                      const deadline = performance.now() + timeoutMs;
                      const poll = () => {
                        try {
                          const value = predicate();
                          if (value) { resolve(value); return; }
                        } catch (error) { reject(error); return; }
                        if (performance.now() >= deadline) { reject(new Error(`${description} timed out`)); return; }
                        setTimeout(poll, 40);
                      };
                      poll();
                    });
                    const json = async (url, options = {}) => {
                      const response = await fetch(url, {cache: 'no-store', credentials: 'same-origin', ...options});
                      return {status: response.status, body: await response.json()};
                    };
                    const operationPayload = async (url, label) => {
                      const accepted = await json(url);
                      if (accepted.status === 200) return accepted.body?.state === 'ready' ? accepted.body.data : accepted.body;
                      if (accepted.status !== 202 || !accepted.body.operation?.id) throw new Error(`${label} was not accepted: ${JSON.stringify(accepted)}`);
                      for (let attempt = 0; attempt < 160; attempt += 1) {
                        const terminal = await json('/api/operations/' + encodeURIComponent(accepted.body.operation.id));
                        if (terminal.status === 202) {
                          await new Promise(resolve => setTimeout(resolve, 25));
                          continue;
                        }
                        if (terminal.status !== 200 || terminal.body.state !== 'ready') throw new Error(`${label} terminal: ${JSON.stringify(terminal)}`);
                        return terminal.body.data;
                      }
                      throw new Error(`${label} timed out`);
                    };
                    (async () => {
                      const item = await openFileInEditor(path, {name: 'note.txt'}, {userInitiated: true, viewMode: 'edit', includeGit: true});
                      const panel = () => fileEditorPanelsForPath(path)[0];
                      await waitFor(() => panel()?._cmView?.state?.doc?.toString?.() === 'working\\n', 'TextEdit Git fixture open');
                      const state = fileEditorStateForItem(path, item);
                      if (!state) throw new Error('TextEdit Git state is unavailable');
                      const diffButton = panel()?.querySelector('.file-editor-diff-panel');
                      if (!diffButton || diffButton.hidden || diffButton.disabled) {
                        throw new Error('TextEdit Git Differ control is not rendered or enabled: ' + JSON.stringify({
                          present: Boolean(diffButton),
                          hidden: diffButton?.hidden ?? null,
                          disabled: diffButton?.disabled ?? null,
                          state: {gitRoot: state.gitRoot || '', gitTracked: state.gitTracked === true, gitHasHistory: state.gitHasHistory === true},
                        }));
                      }
                      const beforeMode = editorViewModeFor(path, item);
                      diffButton.click();
                      let lastDiffDiagnostics = {};
                      try {
                        await waitFor(() => {
                          const currentPanel = panel();
                          const diffContainer = currentPanel?.querySelector('.file-editor-diff-codemirror');
                          const mergeView = diffContainer?.querySelector('.cm-editor');
                          const currentState = fileEditorStateForItem(path, item);
                          lastDiffDiagnostics = {
                            mode: editorViewModeFor(path, item),
                            diffLoaded: currentState?.diffLoaded ?? null,
                            diffLoading: currentState?.diffLoading ?? null,
                            diffUnavailable: currentState?.diffUnavailable ?? null,
                            diffError: currentState?.diffError || '',
                            container: Boolean(diffContainer),
                            hidden: diffContainer?.hidden ?? null,
                            mergeView: Boolean(mergeView),
                            width: diffContainer?.clientWidth || 0,
                            height: diffContainer?.clientHeight || 0,
                            panelText: currentPanel?.textContent?.slice(0, 240) || '',
                          };
                          return beforeMode !== 'diff'
                            && lastDiffDiagnostics.mode === 'diff'
                            && lastDiffDiagnostics.diffLoaded === true
                            && lastDiffDiagnostics.diffUnavailable !== true
                            && lastDiffDiagnostics.hidden === false
                            && lastDiffDiagnostics.mergeView
                            && lastDiffDiagnostics.width > 0
                            && lastDiffDiagnostics.height > 0;
                        }, 'rendered TextEdit Git Differ view');
                      } catch (error) {
                        throw new Error(`${error.message}: ${JSON.stringify(lastDiffDiagnostics)}`);
                      }
                      const currentPanel = panel();
                      const currentState = fileEditorStateForItem(path, item);
                      const diffContainer = currentPanel?.querySelector('.file-editor-diff-codemirror');
                      const mergeView = diffContainer?.querySelector('.cm-editor');
                      const renderedText = diffContainer?.textContent || '';
                       if (currentState?.diffFromRef !== 'HEAD' || currentState?.diffToRef !== 'current' || !String(currentState?.diffOriginal).includes('changed') || currentState?.diffWorking !== 'working\\n' || !String(currentState?.diff).includes('-changed') || !String(currentState?.diff).includes('+working') || !renderedText.includes('working') || !mergeView) {
                        throw new Error('TextEdit Git rendered Differ contract mismatch: ' + JSON.stringify({
                          beforeMode,
                          mode: editorViewModeFor(path, item),
                          diffLoaded: currentState?.diffLoaded,
                          diffUnavailable: currentState?.diffUnavailable,
                          from: currentState?.diffFromRef,
                          to: currentState?.diffToRef,
                          original: currentState?.diffOriginal,
                          working: currentState?.diffWorking,
                          diff: currentState?.diff,
                          renderedText: renderedText.slice(0, 300),
                          mergeView: Boolean(mergeView),
                          width: diffContainer?.clientWidth || 0,
                          height: diffContainer?.clientHeight || 0,
                        }));
                      }
                      const history = await operationPayload('/api/fs/git-history?path=' + encodeURIComponent(repo) + '&limit=10', 'Git history');
                      if (history.repo !== repo || history.head !== head || !Array.isArray(history.commits) || history.commits.length < 2) throw new Error('Git history contract mismatch: ' + JSON.stringify(history));
                      const commit = await operationPayload('/api/fs/git-commit?path=' + encodeURIComponent(repo) + '&commit=' + encodeURIComponent(head) + '&head=' + encodeURIComponent(head), 'Git commit');
                      if (commit.to_ref !== head || !Array.isArray(commit.files) || !commit.files.some(file => file.path === 'note.txt')) throw new Error('Git commit contract mismatch: ' + JSON.stringify(commit));
                      const blame = await operationPayload('/api/batch/blame?path=' + encodeURIComponent(path), 'Git blame');
                      if (blame.in_repo !== true || !blame.lines?.['1']) throw new Error('Git blame contract mismatch: ' + JSON.stringify(blame));
                      done({diff: {clicked: true, beforeMode, mode: editorViewModeFor(path, item), rendered: true, from: currentState.diffFromRef, to: currentState.diffToRef, original: currentState.diffOriginal, working: currentState.diffWorking, changed: String(currentState.diff).includes('-changed'), workingChange: String(currentState.diff).includes('+working'), width: diffContainer.clientWidth, height: diffContainer.clientHeight}, history: {head: history.head, commits: history.commits.length}, commit: {to: commit.to_ref, files: commit.files.length}, blame: {inRepo: blame.in_repo, lines: Object.keys(blame.lines).length}});
                    })().catch(error => done({error: String(error?.stack || error)}));
                """, str(git_note), str(git_root), git_head)
                if git_views.get('error'):
                    raise SystemExit(f"Go later-route Chromium E2E failed: {git_views}")
                (finder_root / "external.txt").write_text("external edit\n", encoding="utf-8")
                watch = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const root = arguments[0];
                    const token = arguments[1];
                    const check = async () => {
                      const response = await fetch('/api/fs/watch-diff?since=' + encodeURIComponent(token), {cache: 'no-store', credentials: 'same-origin'});
                      const body = await response.json();
                      if (response.status === 200 && body.directories?.some(item => item.path === root && item.data.entries?.some(entry => entry.name === 'external.txt'))) {
                        done({changed: true, mode: body.mode, paths: body.change_summary?.paths_changed});
                        return;
                      }
                      setTimeout(check, 100);
                    };
                    check().catch(error => done({error: String(error)}));
                    setTimeout(() => done({error: 'watch diff timeout'}), 5000);
                """, str(finder_root), finder['watchToken'])
                if watch.get('error') or watch.get('changed') is not True:
                    raise SystemExit(f"Go Phase 4 Finder watch Chromium E2E failed: {watch}")
                repair = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const sessions = arguments[0];
                    const stream = new EventSource('/api/client-events?channels=events&client_id=repair-e2e');
                    const seen = [];
                    let settled = false;
                    const finish = value => { if (settled) return; settled = true; stream.close(); done(value); };
                    stream.addEventListener('ready', () => {
                        Promise.all(sessions.map((session, index) => fetch('/api/event', {
                            method: 'POST', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({session, type: 'repair-' + index, message: 'phase3 repair event'})
                        }).then(response => response.json().then(body => ({status: response.status, body})))))
                          .then(results => seen.push({published: results}));
                    });
                    stream.addEventListener('event_log_changed', event => {
                        const payload = JSON.parse(event.data);
                        seen.push(payload);
                        if ((payload.repair_resources || []).length >= 2) finish({seen});
                    });
                    setTimeout(() => finish({error: 'repair timeout', seen}), 7000);
                """, sessions)
                if repair.get('error') or not any((item.get('repair_resources') or []) for item in repair.get('seen', []) if isinstance(item, dict)):
                    raise SystemExit(f"Go Phase 3 Chromium event repair failed: {repair}")
                operation = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const id = 'op-chrome-replay';
                    fetch('/api/operations/accept', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id, request_id:'chrome-request', route:'POST /api/operations/accept', kind:'e2e'})})
                      .then(response => response.json().then(body => ({status: response.status, body})))
                      .then(accepted => fetch('/api/operations/terminalize', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id, result:{state:'ready', data:{source:'chrome'}}, status:200})}).then(response => response.json().then(body => ({accepted, status: response.status, body}))))
                      .then(done);
                """)
                if operation['accepted']['status'] != 202 or operation['status'] != 200:
                    raise SystemExit(f"Go Phase 3 operation setup failed: {operation}")
                first_pid = proc.pid
                stop_server(force=True)
                proc = start_server()
                if proc.pid == first_pid:
                    raise SystemExit(f"Go Phase 3 restart reused server pid {first_pid}")
                assert_server_identity(driver, proc)
                replay = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const seen = [];
                    const stream = new EventSource('/api/client-events?operation_id=op-chrome-replay');
                    stream.addEventListener('ready', event => seen.push('ready'));
                    stream.addEventListener('operation_terminal', event => {
                        const payload = JSON.parse(event.data).payload;
                        seen.push({type:'terminal', payload});
                        stream.close();
                        fetch('/api/operations/ack', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({acks:[{id:'op-chrome-replay', cursor:payload.operation.cursor}]})}).then(response => response.json().then(body => done({seen, ack:body})));
                    });
                    setTimeout(() => { stream.close(); done({error:'replay timeout', seen}); }, 5000);
                """)
                if replay.get('error') or 'ready' not in replay['seen'] or not any(item.get('type') == 'terminal' for item in replay['seen'] if isinstance(item, dict)) or 'op-chrome-replay' not in replay['ack'].get('acknowledged', []):
                    raise SystemExit(f"Go Phase 3 restart replay/ack failed: {replay}")
                second_pid = proc.pid
                stop_server(force=True)
                proc = start_server()
                if proc.pid == second_pid:
                    raise SystemExit(f"Go Phase 3 second restart reused server pid {second_pid}")
                assert_server_identity(driver, proc)
                no_replay = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const stream = new EventSource('/api/client-events?operation_id=op-chrome-replay');
                    const seen = [];
                    stream.addEventListener('ready', () => seen.push('ready'));
                    stream.addEventListener('operation_terminal', () => { stream.close(); done({error:'acknowledged operation replayed', seen}); });
                    setTimeout(() => { stream.close(); done({seen}); }, 1500);
                """)
                if no_replay.get('error') or no_replay.get('seen') != ['ready']:
                    raise SystemExit(f"Go Phase 3 acknowledged replay failed: {no_replay}")
                ui_launch_cases = (
                    ("bash", "bash"),
                    ("new Claude", "claude"),
                    ("new Codex", "codex"),
                    ("new Claude (dangerously auto approve)", "claude --dangerously-skip-permissions"),
                    ("new Codex (dangerously auto approve)", "codex --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust"),
                )
                for label, command in ui_launch_cases:
                    click_result = driver.execute_script("""
                        const label = arguments[0];
                        const wrapper = document.querySelector('[data-app-menu="file"]');
                        if (!wrapper) return {error: 'File menu is not rendered'};
                        const button = wrapper.querySelector(':scope > .app-menu-button');
                        if (!button) return {error: 'File menu button is not rendered'};
                        button.click();
                        const normalize = node => (node.querySelector('.app-menu-label')?.textContent || node.textContent || '').replace(/\\s+/g, ' ').trim();
                        const target = Array.from(wrapper.querySelectorAll(':scope > .app-menu-popover .app-menu-command')).find(node => normalize(node) === label);
                        if (!target) return {error: `File menu command not found: ${label}`, labels: Array.from(wrapper.querySelectorAll(':scope > .app-menu-popover .app-menu-command')).map(normalize)};
                        target.click();
                        return {clicked: label};
                    """, label)
                    if click_result.get("error"):
                        raise SystemExit(f"Go File menu -> New Chromium contract failed: {click_result}")
                    recorded = wait_for_recorded_command(command)
                if not expected_launch_commands.issubset(recorded):
                    raise SystemExit(f"Go File -> New launch arguments did not reach fixture processes: {sorted(recorded)!r}")
                stop_server(force=True)
                proc = start_server(dangerously_yolo=False)
                driver.get(f"https://127.0.0.1:{port}/?sessions={session}&layout=left&tabs=left:{session}")
                deadline = time.time() + 10
                while time.time() < deadline:
                    if driver.find_elements("css selector", '[data-app-menu="file"] > .app-menu-button'):
                        break
                    time.sleep(0.1)
                non_yolo_contract = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const session = arguments[0];
                    (async () => {
                      try {
                        const response = await fetch('/api/create-session?agent=codex&dangerously_yolo=1&session=' + encodeURIComponent(session + '-denied'), {method: 'POST', cache: 'no-store', credentials: 'same-origin'});
                        const body = await response.json();
                        const bootstrap = JSON.parse((document.getElementById('yomux-bootstrap') || document.getElementById('yolomux-bootstrap'))?.textContent || '{}');
                        const wrapper = document.querySelector('[data-app-menu="file"]');
                        const button = wrapper?.querySelector(':scope > .app-menu-button');
                        button?.click();
                        const normalize = node => (node.querySelector('.app-menu-label')?.textContent || node.textContent || '').replace(/\\s+/g, ' ').trim();
                        const labels = Array.from(wrapper?.querySelectorAll(':scope > .app-menu-popover .app-menu-command') || []).map(normalize);
                        done({status: response.status, body, dangerouslyYolo: bootstrap.dangerouslyYolo, labels});
                      } catch (error) {
                        done({error: String(error?.stack || error)});
                      }
                    })();
                """, session)
                forbiddenMessage = "full-access agent launches require YOmux --dangerously-yolo"
                if (non_yolo_contract.get("error") or non_yolo_contract.get("status") != 403 or non_yolo_contract.get("body", {}).get("error") != forbiddenMessage or non_yolo_contract.get("dangerouslyYolo") is not False or any("dangerously" in label for label in non_yolo_contract.get("labels", []))):
                    raise SystemExit(f"Go non-dangerous launch contract failed: {non_yolo_contract}")

                def selected_tmux_window():
                    return subprocess.check_output(
                        (*tmux, "display-message", "-p", "-t", f"={session}:", "#{window_index}"),
                        text=True,
                    ).strip()

                window_click = WebDriverWait(driver, 10).until(
                    lambda current_driver: current_driver.execute_script(
                        """
                        const session = arguments[0];
                        const button = Array.from(document.querySelectorAll('.panel-detail-row [data-window-index="1"]'))
                          .find(candidate => candidate.getAttribute('data-window-session') === session
                            && candidate.isConnected
                            && candidate.getClientRects().length > 0);
                        if (!button) return false;
                        button.click();
                        return {clicked: true, label: button.textContent.trim()};
                        """,
                        session,
                    )
                )
                window_switch = None
                window_switch_deadline = time.time() + 8
                while time.time() < window_switch_deadline:
                    selected = selected_tmux_window()
                    active = driver.execute_script(
                        """
                        const button = document.querySelector('.panel-detail-row [data-window-index="1"]');
                        return button?.getAttribute('data-window-session') === arguments[0]
                          && button?.classList.contains('active')
                          && button?.getAttribute('aria-pressed') === 'true';
                        """,
                        session,
                    )
                    if selected == "1" and active:
                        window_switch = {"selected": selected, "button_active": active}
                        break
                    time.sleep(0.1)
                if window_switch is None:
                    button_html = driver.execute_script(
                        "return document.querySelector('.panel-detail-row [data-window-index=\\\"1\\\"]')?.outerHTML || null;"
                    )
                    raise SystemExit(
                        f"Go Chromium tmux window button failed: selected={selected_tmux_window()!r}, button={button_html!r}"
                    )

                exited_session = session
                roster_options = Options()
                roster_options.binary_location = chrome_binary
                for arg in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--ignore-certificate-errors", "--window-size=1200,800"):
                    roster_options.add_argument(arg)
                roster_driver = webdriver.Chrome(
                    service=Service(executable_path=driver_path), options=roster_options,
                ) if driver_path else webdriver.Chrome(options=roster_options)
                roster_lease = WebDriverLease.from_driver(roster_driver)
                try:
                    roster_driver.get(f"https://127.0.0.1:{port}/login")
                    roster_driver.find_element("name", "username").send_keys("e2e")
                    roster_driver.find_element("name", "password").send_keys("test-password")
                    roster_driver.find_element("css selector", "button[type=submit]").click()
                    WebDriverWait(roster_driver, 10).until(lambda current: "/login" not in current.current_url)
                    roster_driver.execute_script("localStorage.clear(); sessionStorage.clear();")
                    roster_driver.get(f"https://127.0.0.1:{port}/?sessions={exited_session}&layout=left&tabs=left:{exited_session}")
                    try:
                        WebDriverWait(roster_driver, 10).until(lambda current: current.execute_script(
                            "const visible = node => Boolean(node?.getClientRects().length"
                            " && getComputedStyle(node).display !== 'none'"
                            " && getComputedStyle(node).visibility !== 'hidden');"
                            "return visible(document.getElementById(`panel-${arguments[0]}`))"
                            "&& (tabberIsAdvanced()"
                            " ? visible(document.querySelector(`.dockview-pane-tab[data-pane-tab=\"${CSS.escape(arguments[0])}\"]`))"
                            " : dockviewLayoutState.api.groups.every(group => group.panels.length === 1)"
                            " && dockviewLayoutState.api.groups.some(group => group.panels.some(panel => panel.id !== '__tabber__'))"
                            " && dockviewLayoutState.api.groups.filter(group => group.panels.some(panel => panel.id !== '__tabber__'))"
                            " .every(group => group.header.hidden))"
                            "&& clientEventTransportState.connected === true;",
                            exited_session,
                        ))
                    except TimeoutException as error:
                        mount_state = roster_driver.execute_script("""
                            const session = arguments[0];
                            const panel = document.getElementById(`panel-${session}`);
                            const tab = document.getElementById(`panel-tab-${session}`);
                            return {
                              url: location.href,
                              sessions: typeof sessions === 'undefined' ? null : sessions.slice(),
                              activeSessions: typeof activeSessions === 'undefined' ? null : activeSessions.slice(),
                              panel: panel ? {rect: panel.getBoundingClientRect().toJSON(), display: getComputedStyle(panel).display} : null,
                              tab: tab ? {rect: tab.getBoundingClientRect().toJSON(), display: getComputedStyle(tab).display} : null,
                              transport: typeof clientEventTransportState === 'undefined' ? null : clientEventTransportState.connected,
                              body: document.body?.innerText?.slice(0, 400) || '',
                            };
                        """, exited_session)
                        raise SystemExit(f"Go roster-exit E2E could not mount its clean fixture pane: {mount_state}") from error
                    roster_channels = roster_driver.execute_script("""
                        const source = clientEventTransportState.source;
                        if (!source?.url) return [];
                        return new URL(source.url, location.href).searchParams.get('channels')?.split(',') || [];
                    """)
                    # Fresh Normal installs display Tabber, which owns transcript demand.
                    roster_tabber_visible = roster_driver.execute_script("return activePaneItems().includes(tabberItemId);")
                    if "core" not in roster_channels or ("transcripts" in roster_channels) != roster_tabber_visible:
                        raise SystemExit(
                            f"Go roster-exit E2E has the wrong client-event demand: {roster_channels}"
                        )
                    exited_slot = roster_driver.execute_script("return slotForItem(arguments[0], layoutSlots);", exited_session)
                    kill_result = subprocess.run(
                        (*tmux, "kill-session", "-t", f"={exited_session}"),
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    if kill_result.returncode != 0:
                        raise SystemExit(
                            f"Go roster-exit E2E could not externally exit {exited_session!r}: "
                            f"{kill_result.stderr.strip()}"
                        )
                    roster_exit = roster_driver.execute_async_script("""
                    const session = arguments[0];
                    const exitedSlot = arguments[1];
                    const done = arguments[arguments.length - 1];
                    const startedAt = performance.now();
                    const deadline = startedAt + 5000;
                    const visible = node => {
                      if (!node || !node.getClientRects().length) return false;
                      const style = getComputedStyle(node);
                      return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) > 0;
                    };
                    const pane = () => document.getElementById(`panel-${session}`);
                    const tab = () => document.querySelector(`.dockview-pane-tab[data-pane-tab="${CSS.escape(session)}"]`);
                    const poll = () => {
                      const advanced = tabberIsAdvanced();
                      const dropPromptVisible = Array.from(document.querySelectorAll('.empty-pane-panel strong'))
                        .some(node => visible(node) && node.textContent.trim() === 'Drop a tab here');
                      const normalConversationItems = activePaneItems().filter(item => item !== tabberItemId);
                      const normalPaneContract = normalConversationItems.length <= 1
                        && !normalConversationItems.includes(session) && !dropPromptVisible;
                      if (!sessions.includes(session) && !activeSessions.includes(session)
                          && !visible(tab())
                          && !visible(pane())
                          && (advanced ? paneIsPlaceholder(exitedSlot, layoutSlots) : normalPaneContract)) {
                        done({ok: true, elapsedMs: performance.now() - startedAt, advanced, placeholderSlot: exitedSlot});
                        return;
                      }
                      if (performance.now() >= deadline) {
                        done({ok: false, sessions: sessions.slice(), activeSessions: activeSessions.slice(),
                          panelVisible: visible(pane()),
                          tabVisible: visible(tab()),
                          panelRect: pane()?.getBoundingClientRect().toJSON() || null,
                          tabRect: tab()?.getBoundingClientRect().toJSON() || null,
                          placeholder: paneIsPlaceholder(exitedSlot, layoutSlots),
                          advanced, normalPaneContract, normalConversationItems,
                          transport: clientEventTransportState?.connected});
                        return;
                      }
                      setTimeout(poll, 25);
                    };
                    poll();
                    """, exited_session, exited_slot)
                    if not roster_exit.get("ok"):
                        raise SystemExit(f"Go roster-exit E2E left an exited pane visible: {roster_exit}")
                    roster_exit["eventChannels"] = roster_channels
                    print(f"Go external tmux exit convergence: {json.dumps(roster_exit, sort_keys=True)}")
                finally:
                    retirement = roster_lease.retire()
                    if not retirement.proven_gone:
                        cleanup_errors.append(f"Go roster-exit E2E chromedriver survived retirement: {retirement.as_dict()}")

                stop_server(force=True)
                journal_path = config / "go-operations.jsonl"
                journal_lines = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                if not any(line.get("type") == "snapshot" and any(item.get("id") == "op-chrome-replay" and item.get("delivery_acknowledged") is True for item in line.get("operations", [])) for line in journal_lines):
                    raise SystemExit(f"Go Phase 3 journal ack state missing: {journal_lines}")
                print({'terminal': result, 'editor': editor, 'textedit_conflict': conflict, 'viewedit': markdown, 'viewedit_reopen': markdown_reopened, 'finder': finder, 'finder_ui': finder_ui, 'git_views': git_views, 'finder_watch': watch, 'event_repair': repair, 'operation_replay': replay, 'after_ack_restart': no_replay, 'pids': [first_pid, second_pid, proc.pid if proc else None], 'journal_records': len(journal_lines), 'binary_sha256': binary_hash, 'served_bundle_sha256': served_bundle_hashes})
            finally:
                if driver_lease is not None:
                    retirement = driver_lease.retire()
                    if not retirement.proven_gone:
                        cleanup_errors.append(f"Go browser E2E chromedriver survived retirement: {retirement.as_dict()}")
                elif driver is not None:
                    try:
                        driver.quit()
                    except WebDriverException as error:
                        cleanup_errors.append(f"Go browser E2E WebDriver cleanup failed: {error}")
                driver = None
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except WebDriverException as error:
                    cleanup_errors.append(f"Go browser E2E WebDriver cleanup failed: {error}")
            stop_server()
            all_sessions = set(created_sessions)
            listed_sessions = subprocess.run((*tmux, "list-sessions", "-F", "#S"), check=False, capture_output=True, text=True)
            if listed_sessions.returncode == 0:
                all_sessions.update(line.strip() for line in listed_sessions.stdout.splitlines() if line.strip())
            elif listed_sessions.returncode != 1:
                cleanup_errors.append(f"could not list fixture tmux sessions during cleanup: {listed_sessions.stderr.strip()}")
            for session_name in sorted(all_sessions):
                cleanup_tmux_session(session_name)
            cleanup_tmux_socket_dir()
            if cleanup_errors:
                raise SystemExit("Go browser E2E cleanup failed: " + "; ".join(cleanup_errors))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Docker/Bazel Go Chromium E2E contract.")
    parser.add_argument("binary", nargs="?", help="path to the Bazel-built Go server binary")
    arguments = parser.parse_args()
    binary = arguments.binary or product_env('GO_BINARY', environ=os.environ)
    if not binary:
        parser.error("a Go server binary argument or YOMUX_GO_BINARY is required")
    main(binary)
