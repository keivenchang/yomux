#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Exercise a selected published base -> candidate upgrades in private native Mac fixtures."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait
from tools.browser_runtime import resolve_browser_executables
from tools.bazel_execution_contract import native_bazel_startup_flags
from tools.verify_go_agent_status_live import make_opencode_database

AUTH = {"Authorization": "Basic " + base64.b64encode(b"upgrade:fixture-password").decode()}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def required_tool(name):
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"required upgrade verification tool is unavailable: {name}")
    return path


def release_version(text):
    match = re.search(r'version:\s*[\"\']?([0-9]+\.[0-9]+\.[0-9]+)', text)
    if match is None:
        raise ValueError("version.yaml must contain a numeric release version")
    return tuple(map(int, match.group(1).split(".")))


def newest_base_tag(tags, candidate_version):
    releases = [(tuple(map(int, tag[1:].split("."))), tag) for tag in tags if re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag)]
    eligible = [(version, tag) for version, tag in releases if version < candidate_version]
    require(bool(eligible), "no reachable release tag below candidate; select --base-tag")
    return max(eligible)[1]


def request(base, path, payload=None):
    headers = dict(AUTH)
    body = None if payload is None else json.dumps(payload).encode()
    if body is not None:
        headers["Content-Type"] = "application/json"
    with urllib.request.urlopen(urllib.request.Request(base + path, data=body, headers=headers), timeout=5) as response:
        return json.load(response)


def wait_for(probe, timeout, description):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = probe()
        if result:
            return result
        time.sleep(0.2)
    raise TimeoutError(description)


class UpgradeGate:
    def __init__(self, source, output, resume=False):
        self.source, self.output = source, output
        self.home = output / "home"
        self.home.mkdir(mode=0o700, exist_ok=resume)
        self.env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "SHELL") if key in os.environ}
        self.env.update(HOME=str(self.home), TMPDIR=str(output), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
        self.env.update(GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid", GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
        self.env["YOMUX_TEST_DISABLE_PRICING_REFRESH"] = "1"
        # The published entry point predates shared JVM selection. Apply the same
        # admitted startup flags without substituting any build action.
        tools = output / "tools"
        tools.mkdir(mode=0o700, exist_ok=resume)
        bazel = required_tool("bazel")
        command = shlex.join([bazel, *native_bazel_startup_flags()])
        (tools / "bazel").write_text('#!/bin/sh\nexec ' + command + ' "$@"\n')
        (tools / "bazel").chmod(0o700)
        # Managed boot intentionally clears socket environment overrides. Keep
        # its tmux commands on this fixture's private socket through PATH too.
        tmux = shlex.join([required_tool("tmux"), "-S", str(output / "tmux.sock")])
        (tools / "tmux").write_text('#!/bin/sh\nexec ' + tmux + ' "$@"\n')
        (tools / "tmux").chmod(0o700)
        self.env["PATH"] = str(tools) + os.pathsep + self.env["PATH"]
        self.commands = max((int(p.stem.split("-")[1]) for p in output.glob("command-*.log")), default=0)
        if resume:
            prior = output / "result.json"
            prior.rename(output / f"result-before-{self.commands}.json")
        self.servers = []
        self.browser = None
        self.origin = output / "origin"
        self.results = {"platform": platform.platform(), "cases": []}

    def run(self, *args, cwd=None, expected=0, env=None, data=None):
        self.commands += 1
        log = self.output / f"command-{self.commands:03}.log"
        with log.open("wb") as stream:
            try:
                result = subprocess.run(list(map(str, args)), cwd=cwd, env=env or self.env, input=data, stdout=stream, stderr=subprocess.STDOUT, timeout=1200)
            except subprocess.TimeoutExpired as error:
                raise TimeoutError(f"{args[0]} timed out after 1200 seconds; see {log}") from error
        if result.returncode != expected:
            raise RuntimeError(f"{args[0]} exited {result.returncode}; expected {expected}; see {log}")
        return log.read_bytes()

    def git(self, root, *args):
        return self.run("git", "-C", root, *args).decode().strip()

    def prepare(self, snapshot, candidate_ref, base_tag):
        candidate_ref = self.git(self.source,"rev-parse", candidate_ref + "^{commit}")
        if not self.origin.exists():
            self.run("git", "clone", "--no-hardlinks", "--no-checkout", self.source, self.origin)
        candidate_version = release_version(self.git(self.source, "show", candidate_ref + ":version.yaml"))
        if base_tag is None:
            base_tag = newest_base_tag(self.git(self.source, "tag", "--merged", candidate_ref).splitlines(), candidate_version)
        require(bool(re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", base_tag)), "base tag must be a numeric vMAJOR.MINOR.PATCH release")
        self.base_version = base_tag[1:]
        self.git(self.source, "merge-base", "--is-ancestor", base_tag, candidate_ref)
        self.git(self.origin, "checkout", "-B", "main", base_tag)
        self.base_sha = self.git(self.origin, "rev-parse", "HEAD")
        if snapshot:
            require(self.git(self.source, "rev-parse", "HEAD") == self.base_sha, "snapshot requires a checkout matching --base-tag")
        # Reuse verified dependency bytes, never operator configuration or credentials.
        for name in ("static", "node_modules/.yomux"):
            if (self.source / name).is_dir():
                shutil.copytree(self.source / name, self.origin / name, dirs_exist_ok=True)
        cache = Path.home() / "Library/Caches/yomux/vendor"
        if cache.is_dir():
            shutil.copytree(cache, self.home / "Library/Caches/yomux/vendor", dirs_exist_ok=True)
        self.run("bash", "tools/build_go.sh", cwd=self.origin)
        binary = self.origin / "bazel-bin/cmd/yomux-go/yomux-go_/yomux-go"
        self.old_binary = self.output / "published/yomux-go"
        self.old_binary.parent.mkdir(mode=0o700,exist_ok=True)
        replacement = self.old_binary.with_name("yomux-go.next-" + str(os.getpid()))
        shutil.copy2(binary, replacement)
        replacement.replace(self.old_binary)
        require(self.run(self.old_binary, "--version").strip() == f"yomux-go {self.base_version}".encode(), 'upgrade verification failed: self.run(self.old_binary, "--version").strip() == f"yomux-go {self.base_version}".encode()')
        generated = self.output / "base-generated"
        self.run(self.origin / "bazel-bin/tools/staticgen/staticgen_/staticgen", "--source-root", self.origin, "--output-directory", generated)
        shutil.copytree(generated, self.origin / "static", dirs_exist_ok=True)
        self.base_static = self.output / "base-static"
        shutil.copytree(self.origin / "static", self.base_static, dirs_exist_ok=True)
        if snapshot:
            require(self.git(self.source, "rev-parse", "HEAD") == self.base_sha, "snapshot requires the published base")
            patch = self.run("git", "-C", self.source, "diff", "HEAD", "--binary")
            self.run("git", "-C", self.origin, "apply", data=patch)
            manifest = self.origin / "version.yaml"
            major, minor, patch = release_version(manifest.read_text())
            manifest.write_text(f'version: "{major}.{minor}.{patch + 1}"\n')
            self.git(self.origin, "add", "-u")
            self.git(self.origin, "-c", "commit.gpgsign=false", "commit", "-m", "Disposable candidate snapshot")
            self.results["snapshot_only"] = True
        else:
            self.git(self.origin, "checkout", "-B", "main", candidate_ref)
            require(self.git(self.source, "status", "--porcelain") == "", "release gate requires a clean candidate")
            self.results["snapshot_only"] = False
        self.candidate_sha = self.git(self.origin, "rev-parse", "HEAD")
        self.target = re.search(r'"([0-9]+\.[0-9]+\.[0-9]+)"', (self.origin / "version.yaml").read_text()).group(1)
        require(release_version(f"version: {self.target}") > release_version(f"version: {self.base_version}"), "candidate must advertise the next release")
        self.results.update(base_tag=base_tag, base_sha=self.base_sha, candidate_sha=self.candidate_sha, target=self.target)
        self.git(self.origin, "remote", "set-url", "origin", str(self.origin))
        # Only the affected historical base needs the original archive-writer control.
        self.broken_sha = None
        if self.base_version != "0.11.0":
            return
        # Negative control uses the published asset writer and entry point.
        entry = self.run("git", "-C", self.origin, "show", f"{self.base_sha}:tools/build_go.sh")
        (self.origin / "tools/build_go.sh").write_bytes(entry)
        path = self.origin / "cmd/yomux-go/launch_native.go"
        source = path.read_text()
        start = source.index("func nativeAssetWriterSlot(")
        end = source.index("func validateUpdateStage(", start)
        source = source[:start] + '''func nativeAssetWriterSlot(root string) (string, error) {
 output, err := nativeRepositoryPath(root, "--absolute-git-dir")
 if err != nil { return "", err }
 return filepath.Join(strings.TrimSpace(string(output)), "yolomux", "worktree-writer"), nil
}

''' + source[end:]
        path.write_text(source)
        self.git(self.origin, "add", "-u")
        self.git(self.origin, "-c", "commit.gpgsign=false", "commit", "-m", "Disposable original-writer negative control")
        self.broken_sha = self.git(self.origin, "rev-parse", "HEAD")

    def case(self, name, installed=False, negative=False):
        previous = sorted(self.output.glob(name + "-*"))
        case = previous[-1] if previous else self.output / f"{name}-{self.commands}"
        case.mkdir(mode=0o700, exist_ok=True)
        checkout = case / "source"
        if checkout.exists():
            require(self.git(checkout, "status", "--porcelain") == "", 'upgrade verification failed: self.git(checkout, "status", "--porcelain") == ""')
            self.git(checkout, "checkout", "--detach", self.base_sha)
        else:
            self.git(self.origin, "worktree", "add", "--detach", checkout, self.base_sha)
        shutil.copytree(self.base_static, checkout / "static", dirs_exist_ok=True)
        shutil.copytree(self.origin / "node_modules/.yomux", checkout / "node_modules/.yomux", dirs_exist_ok=True)
        config = case / "config"
        config.mkdir(mode=0o700, exist_ok=True)
        (config / "auth.yaml").write_text("users:\n  - username: upgrade\n    password: fixture-password\n    role: admin\n")
        (config / "auth.yaml").chmod(0o600)
        (config / "settings.yaml").write_text("updates:\n  notify_level: patch\n")
        env = dict(self.env)
        env.update(YOMUX_CONFIG_DIR=str(config), YOMUX_STATE_DIR=str(case / "state"), YOMUX_CACHE_DIR=str(case / "cache"), YOMUX_RUNTIME_DIR=str(case / "runtime"), YOMUX_LOG_DIR=str(case / "logs"), YOMUX_START_LOCK_DIR=str(case / "start-locks"), YOMUX_CA_DIR=str(case / "ca"), YOMUX_TMUX_SOCKET=str(case / "tmux.sock"))
        for directory in (case / "claude", case / "codex"):
            directory.mkdir(mode=0o700, exist_ok=True)
        if not (case / "opencode.sqlite3").exists():
            make_opencode_database(case / "opencode.sqlite3", int(time.time() * 1000))
        env.update(YOMUX_CLAUDE_TRANSCRIPT_ROOT=str(case / "claude"), YOMUX_CODEX_TRANSCRIPT_ROOT=str(case / "codex"), YOMUX_OPENCODE_DATABASE=str(case / "opencode.sqlite3"))
        # A published source instance must satisfy its actual startup contract,
        # including ignored emoji/build inputs used on its later managed restart.
        self.run(self.old_binary, "--operator", "startup-assets", "--root", checkout, env=env)
        root = checkout
        if installed:
            env["YOMUX_GO_BINARY"] = str(self.old_binary)
            self.run("bash", checkout / "tools/install.sh", "--prefix", case / "prefix", "--source-root", checkout, cwd=checkout, env=env)
            root = case / "prefix/lib/yomux"
            env.pop("YOMUX_GO_BINARY")
        server_log = (case / "server.log").open("ab")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            require(port not in (8880, 8881, 7220, 7221), 'upgrade verification failed: port not in (8880, 8881, 7220, 7221)')
            base = f"http://127.0.0.1:{port}"
            process = subprocess.Popen([str(self.old_binary), "--http", "--host", "127.0.0.1", "--port", str(port), "--listen-fd", str(listener.fileno()), "--root", str(root), "--config", str(config), "--stats", str(case / "stats.sqlite3")], cwd=root, env=env, pass_fds=(listener.fileno(),), stdout=server_log, stderr=subprocess.STDOUT)
        self.servers.append((process, base, root, server_log, case))

        def health():
            if process.poll() is not None:
                raise RuntimeError(f"published server exited; see {case / 'server.log'}")
            try:
                return request(base, "/healthz")
            except (OSError, urllib.error.URLError):
                return None
        old = wait_for(health, 30, "published listener readiness")
        require(old["pid"] == process.pid and old["version"] == self.base_version, 'upgrade verification failed: old["pid"] == process.pid and old["version"] == self.base_version')
        print(f"{name}: published {self.base_version} PID {process.pid}, waiting for preflight", flush=True)
        status = wait_for(lambda: (s if (s := request(base, "/api/update-status"))["build_state"] in ("ready", "failed") else None), 1000, "preflight did not finish")
        require(self.git(checkout, "rev-parse", "HEAD") == self.base_sha, 'upgrade verification failed: self.git(checkout, "rev-parse", "HEAD") == self.base_sha')
        if negative:
            require(status["build_state"] == "failed", status)
            log = config / "self-update/preflight/build.log"
            require(status["target_sha"] == self.broken_sha, 'upgrade verification failed: status["target_sha"] == self.broken_sha')
            text = log.read_text()
            require("resolve asset writer slot:" in text and "exit status 128" in text, text[-1000:])
            require(not (config / "self-update/preflight/.git").exists(), 'upgrade verification failed: not (config / "self-update/preflight/.git").exists()')
            require(request(base, "/healthz")["pid"] == process.pid, 'upgrade verification failed: request(base, "/healthz")["pid"] == process.pid')
            self.results["cases"].append({"name": name, "preflight": "failed", "listener_unchanged": True, "checkout_unchanged": True, "log": str(log), "target_sha": status["target_sha"], "archive_git_exit": 128})
            self.stop_server(self.servers[-1])
            return
        require(status["build_state"] == "ready", status)
        require(status["target_sha"] == self.candidate_sha, 'upgrade verification failed: status["target_sha"] == self.candidate_sha')
        stage = config / "self-update/preflight"
        require(not (stage / ".git").exists(), 'upgrade verification failed: not (stage / ".git").exists()')
        prepared = hashlib.sha256((stage / "static/yomux.js").read_bytes()).hexdigest()
        # Use the real published browser, with an unsaved draft retained across activation.
        self.browser.get(base + "/login")
        wait_for(lambda: self.browser.execute_script("return Boolean(document.querySelector('form[action=\"/login\"] input[name=\"username\"]'))"), 20, "browser login form")
        self.browser.execute_script("const form=document.querySelector('form[action=\"/login\"]'); form.elements.username.value='upgrade'; form.elements.password.value='fixture-password'; form.requestSubmit();")
        wait_for(lambda: self.browser.execute_script("return typeof startSelfUpdateReloadPolling === 'function' && typeof fileState !== 'undefined'"), 30, "browser globals")
        self.browser.execute_script("window.__upgradeGate = true; fileState.set('/upgrade-unsaved', {dirty:true});")
        result = request(base, "/api/self-update", {"target_sha": self.candidate_sha})
        require(result["ok"] and result["restarting"], 'upgrade verification failed: result["ok"] and result["restarting"]')
        job_id = result["job"]["id"]
        self.browser.execute_script("startSelfUpdateReloadPolling(arguments[0], arguments[1], arguments[2]);", self.target, old["instance_id"], job_id)
        print(f"{name}: ready, activating job {job_id}", flush=True)

        def completed():
            try:
                job = request(base, "/api/self-update/status")["job"]
                if job["id"] != job_id:
                    raise AssertionError("another update job replaced the requested job")
                if job["state"] == "failed":
                    raise RuntimeError(job)
                return job if job["state"] == "complete" else None
            except (OSError, urllib.error.URLError):
                return None
        job = wait_for(completed, 120, "managed activation did not complete")
        new = request(base, "/healthz")
        require(new["pid"] != old["pid"] and new["instance_id"] != old["instance_id"] and new["version"] == self.target, 'upgrade verification failed: new["pid"] != old["pid"] and new["instance_id"] != old["instance_id"] and new["version"] == self.target')
        require(self.git(checkout, "rev-parse", "HEAD") == self.candidate_sha, 'upgrade verification failed: self.git(checkout, "rev-parse", "HEAD") == self.candidate_sha')
        with urllib.request.urlopen(urllib.request.Request(base + "/static/yomux.js", headers=AUTH), timeout=5) as response:
            require(hashlib.sha256(response.read()).hexdigest() == prepared, 'upgrade verification failed: hashlib.sha256(response.read()).hexdigest() == prepared')
        wait_for(lambda: self.browser.execute_script("return selfUpdateReloadState.deferredToastShown"), 20, "unsaved edit deferral")
        require(self.browser.execute_script("return window.__upgradeGate === true && fileState.get('/upgrade-unsaved').dirty"), 'upgrade verification failed: self.browser.execute_script("return window.__upgradeGate === true && fileState.get(\'/upgrade-unsaved\').dirty")')
        self.browser.execute_script("fileState.delete('/upgrade-unsaved'); document.activeElement?.blur();")
        wait_for(lambda: self.browser.execute_script("return window.__upgradeGate !== true"), 30, "browser did not reload after draft cleared")
        self.results["cases"].append({"name": name, "preflight": "ready", "job": job, "old_pid": old["pid"], "new_pid": new["pid"], "served_sha256": prepared, "browser_reload": True, "unsaved_deferral": True})
        if not installed:
            self.check_candidate_browser()
        self.stop_server(self.servers[-1])

    def check_candidate_browser(self):
        # The upgrade just loaded the exact candidate bundle. Test its UI with
        # controlled replies so a stale instance and an unrelated job are observable.
        browser = self.browser
        wait_for(lambda: browser.execute_script("return typeof applyUpdateAvailable === 'function' && typeof fileState !== 'undefined'"), 30, "candidate browser globals")
        major, minor, patch = self.target.split(".")
        next_version = f"{major}.{minor}.{int(patch) + 1}"
        browser.execute_script("""
            const [target, sha] = arguments;
            const status = {available:true, target, target_sha:sha, build_state:'ready'};
            const job = {id:'candidate-browser-job', state:'complete', target_version:target};
            const state = window.__candidateUpgrade = {phase:'unrelated', posts:[], pings:{}};
            const realFetch = apiFetchJson;
            apiFetchJson = async (url, options, ...rest) => {
              const path = url.split('?')[0];
              if (path === '/api/update-status') return status;
              if (path === '/api/self-update') {
                state.posts.push(JSON.parse(options.body).target_sha);
                return {ok:true, restarting:true, target, instance_id:'old', job:{...job, state:'restarting'}};
              }
              if (path === '/api/self-update/status') {
                return {job:state.phase === 'unrelated' ? {...job, id:'another-job'} : job};
              }
              if (path === '/api/ping' && url.includes('selfUpdate=')) {
                state.pings[state.phase] = (state.pings[state.phase] || 0) + 1;
                return {version:target, instance_id:state.phase === 'stale' ? 'old' : 'replacement'};
              }
              return realFetch(url, options, ...rest);
            };
            applyUpdateAvailable(status);
        """, next_version, self.candidate_sha)
        action = (By.CSS_SELECTOR, ".toast-update-available .toast-action")
        WebDriverWait(browser, 10).until(expected_conditions.element_to_be_clickable(action)).click()
        alert = WebDriverWait(browser, 10).until(expected_conditions.alert_is_present())
        require(next_version in alert.text, alert.text)
        alert.accept()
        for phase in ("unrelated", "stale"):
            browser.execute_script("window.__candidateUpgrade.phase = arguments[0];", phase)
            wait_for(lambda: browser.execute_script("""
                const state = window.__candidateUpgrade;
                if (!state) throw new Error('candidate reloaded before verification');
                return state.pings[arguments[0]] >= 2 && selfUpdateReloadState.pending;
            """, phase), 15, f"candidate did not keep polling after {phase} response")
        require(browser.execute_script("return window.__candidateUpgrade.posts") == [self.candidate_sha], 'upgrade verification failed: browser.execute_script("return window.__candidateUpgrade.posts") == [self.candidate_sha]')
        browser.execute_script("""
            fileState.set('/upgrade-unsaved', {dirty:true});
            window.__candidateUpgrade.phase = 'verified';
        """)
        wait_for(lambda: browser.execute_script("return window.__candidateUpgrade && selfUpdateReloadState.deferredToastShown"), 15, "candidate unsaved edit deferral")
        browser.execute_script("fileState.delete('/upgrade-unsaved'); document.activeElement?.blur();")
        wait_for(lambda: browser.execute_script("return !window.__candidateUpgrade"), 30, "candidate browser did not reload after draft cleared")
        self.results["candidate_browser"] = {
            "confirmation_named_target": True, "posted_target_sha": self.candidate_sha,
            "unrelated_job_ignored": True, "stale_instance_ignored": True,
            "unsaved_deferral": True, "browser_reload": True,
        }

    def stop_server(self, record):
        process, base, root, log, case = record
        if log.closed:
            return
        try:
            current = request(base, "/healthz")
        except (OSError, urllib.error.URLError):
            current = None
        if current:
            pid = current["pid"]
        else:
            port = base.rsplit(":", 1)[1]
            census = subprocess.run(["lsof", "-nP", "-iTCP:" + port, "-sTCP:LISTEN", "-t"], capture_output=True, text=True)
            if census.returncode not in (0, 1):
                raise RuntimeError("could not inspect fixture listener ownership")
            listeners = set(census.stdout.split())
            if len(listeners) > 1:
                raise RuntimeError("ambiguous fixture listener ownership")
            pid = int(listeners.pop()) if listeners else (process.pid if process.poll() is None else None)
        if pid is not None:
            # Health can fail during restart. The exact private listener and cwd
            # still establish ownership without selecting unrelated processes.
            cwd = subprocess.check_output(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], text=True)
            require("n" + str(root) + "\n" in cwd, (pid, root, cwd))
            identity = subprocess.check_output(["ps", "-p", str(pid), "-o", "lstart="], text=True)
            os.kill(pid, signal.SIGTERM)
            try:
                if pid == process.pid:
                    process.wait(timeout=20)
                else:
                    wait_for(lambda: not self.pid_alive(pid), 20, "fixture listener did not retire")
            except (TimeoutError, subprocess.TimeoutExpired):
                require(subprocess.check_output(["ps", "-p", str(pid), "-o", "lstart="], text=True) == identity, 'upgrade verification failed: subprocess.check_output(["ps", "-p", str(pid), "-o", "lstart="], text=True) == identity')
                require("n" + str(root) + "\n" in subprocess.check_output(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], text=True), 'upgrade verification failed: "n" + str(root) + "\\n" in subprocess.check_output(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], text=True)')
                os.kill(pid, signal.SIGKILL)
                if pid == process.pid:
                    process.wait(timeout=5)
                else:
                    wait_for(lambda: not self.pid_alive(pid), 5, "fixture listener survived KILL")
        if process.poll() is None:
            process.wait(timeout=20)
        log.close()

    @staticmethod
    def pid_alive(pid):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    def execute(self, snapshot, candidate_ref, base_tag):
        try:
            self.prepare(snapshot, candidate_ref, base_tag)
            browser = resolve_browser_executables()
            options = webdriver.ChromeOptions()
            options.binary_location = browser.chrome
            options.add_argument("--headless=new")
            options.add_argument("--no-first-run")
            options.add_argument("--disable-background-networking")
            self.browser = webdriver.Chrome(service=Service(browser.chromedriver), options=options)
            if self.broken_sha is not None:
                self.case("original-archive-failure", negative=True)
            self.git(self.origin, "checkout", "--detach", self.candidate_sha)
            self.git(self.origin, "branch", "-f", "main", self.candidate_sha)
            self.case("linked-checkout")
            self.case("installed-prefix", installed=True)
            self.results["passed"] = True
        finally:
            failures = []
            if self.browser:
                try:
                    (self.output / "browser-final.html").write_text(self.browser.page_source)
                    self.browser.save_screenshot(str(self.output / "browser-final.png"))
                except Exception as error:  # Artifact capture must not prevent browser retirement.
                    failures.append(str(error))
                try:
                    self.browser.quit()
                except Exception as error:  # Retain every independent cleanup failure.
                    failures.append(str(error))
            for record in self.servers:
                try:
                    self.stop_server(record)
                except Exception as error:  # Continue retiring independent owned fixtures.
                    failures.append(str(error))
            workspaces = [self.origin, *(case / "config/self-update/preflight" for _, _, _, _, case in self.servers)]
            for workspace in workspaces:
                if (workspace / "MODULE.bazel").is_file():
                    try:
                        self.run("bazel", "--bazelrc=/dev/null", "shutdown", cwd=workspace)
                    except Exception as error:  # Fixture-owned worker retirement boundary.
                        failures.append(str(error))
            self.results["cleanup_errors"] = failures
            if failures:
                self.results["passed"] = False
            (self.output / "result.json").write_text(json.dumps(self.results, indent=2) + "\n")
            if failures:
                raise RuntimeError("fixture cleanup failed: " + "; ".join(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-root", type=Path, default=ROOT)
    parser.add_argument("--candidate-ref", default="HEAD")
    parser.add_argument("--base-tag", help="published base tag; defaults to newest reachable release below candidate")
    parser.add_argument("--snapshot", action="store_true", help="qualify a disposable next-patch snapshot of local tracked edits; not release certification")
    parser.add_argument("--resume", action="store_true", help="reuse private fixture output and its compiled baseline; rebuild before testing")
    parser.add_argument("--output", type=Path, required=True, help="new private evidence directory outside all checkouts")
    args = parser.parse_args()
    if platform.system() != "Darwin":
        parser.error("this release gate requires native macOS")
    if args.resume:
        info = args.output.lstat()
        require(info.st_uid == os.getuid() and info.st_mode & 0o077 == 0, 'upgrade verification failed: info.st_uid == os.getuid() and info.st_mode & 0o077 == 0')
        require(not args.output.is_symlink(), 'upgrade verification failed: not args.output.is_symlink()')
        previous = json.loads((args.output / "result.json").read_text())
        require(not previous.get("cleanup_errors"), 'upgrade verification failed: not previous.get("cleanup_errors")')
        require((args.output / "origin/.git").is_dir(), 'upgrade verification failed: (args.output / "origin/.git").is_dir()')
    else:
        args.output.mkdir(mode=0o700)
    gate = UpgradeGate(args.candidate_root.resolve(), args.output.resolve(), args.resume)
    gate.execute(args.snapshot, args.candidate_ref, args.base_tag)
    print(args.output / "result.json")


if __name__ == "__main__":
    main()
