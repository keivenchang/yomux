from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401

@pytest.mark.parametrize(
    "mock_name,user_input,agent",
    [
        ("mock_codex.py", "sleep 10", "codex"),
        ("mock_claude.py", "sleep 10", "claude"),
    ],
)
def test_mock_agent_prompt_payload_renders_ask_attention_in_live_browser(browser, monkeypatch, tmp_path, mock_name, user_input, agent):
    tmux_binary = shutil.which("tmux")
    if not tmux_binary:
        pytest.skip("tmux is not installed")

    paths = isolate_browser_runtime_paths(monkeypatch, tmp_path)
    sock_base = Path("/tmp") / f"yoask-ui-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    sock_base.mkdir(mode=0o700)
    socket_path = sock_base / "s"
    session = f"yb-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv(YOLOMUX_TMUX_SOCKET_ENV, str(socket_path))

    def tmux_cmd(*args, timeout=8):
        return subprocess.run(
            [tmux_binary, "-S", str(socket_path), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def capture():
        return tmux_cmd("capture-pane", "-p", "-t", f"{session}:").stdout or ""

    def wait_until(predicate, timeout=20):
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            last = capture()
            if predicate(last):
                return True, last
            time.sleep(0.4)
        return False, last

    app = None
    try:
        created = tmux_cmd(
            "new-session", "-d", "-s", session, "-x", "120", "-y", "40",
            f"cd {REPO_ROOT} && exec python3 tools/{mock_name}",
        )
        assert created.returncode == 0, f"tmux new-session failed: {created.stderr or created.stdout}"
        booted, pane = wait_until(lambda text: "❯" in text or "›" in text)
        assert booted, f"{mock_name} did not boot to an input prompt:\n{pane}"
        tmux_cmd("send-keys", "-t", f"{session}:", user_input, "Enter")
        prompted, pane = wait_until(lambda text: user_input in text and ("Would you like to run the following command?" in text or "Do you want to proceed?" in text))
        assert prompted, f"{mock_name} did not render an approval prompt after `{user_input}`:\n{pane}"

        app = TmuxWebtermApp([session], dangerously_yolo=False)
        payload = app.auto_approve_session_status(session, capture_bare_session_when_roster=True)
        assert payload["prompt"]["visible"] is True
        assert payload["screen"]["key"] == "approval"
        assert payload["prompt"]["agent"] == agent
        if agent == "codex":
            assert payload["prompt"]["command"] == user_input
        assert payload["prompt"]["signature"]

        auto_approve_payload = {
            "session_order": [session],
            "sessions": {session: payload},
            "rules": {"path": "/home/test/.config/yolomux/yolo-rules.yaml", "source": "default", "rules": [], "errors": []},
        }
        load_live_runtime_boot_fixture(
            browser,
            tmp_path,
            sessions=[session],
            transcript_sessions={session: {"agents": [{"kind": agent}], "panes": []}},
            auto_approve_payload=auto_approve_payload,
        )
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                const session = arguments[0];
                return document.getElementById(`panel-${session}`)
                  && document.getElementById('topbarActivity')?.textContent?.includes('ASK?');
                """,
                session,
            )
        )
        metrics = browser.execute_script(
            """
            const session = arguments[0];
            const panel = document.getElementById(`panel-${session}`);
            const tab = document.getElementById(`panel-tab-${session}`);
            const topbar = document.getElementById('topbarActivity');
            const beforeSocketFrames = (window.__bootSocketInstances || []).flatMap(socket => socket.sent || []);
            const badge = tab?.querySelector('[data-prompt-attention-clear]');
            const before = {
              badgeText: badge?.textContent || '',
              tabAttention: tab?.classList.contains('needs-attention') || false,
              panelNeedsApproval: panel?.classList.contains('needs-exec-pane') || false,
              topbarText: topbar?.textContent || '',
            };
            badge?.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
            const afterSocketFrames = (window.__bootSocketInstances || []).flatMap(socket => socket.sent || []);
            const after = {
              badgeText: tab?.querySelector('[data-prompt-attention-clear]')?.textContent || '',
              tabAttention: tab?.classList.contains('needs-attention') || false,
              panelNeedsApproval: panel?.classList.contains('needs-exec-pane') || false,
              topbarText: topbar?.textContent || '',
              newInputFrames: afterSocketFrames.slice(beforeSocketFrames.length).filter(frame => String(frame).includes('"type":"input"')).length,
            };
            return {before, after};
            """,
            session,
        )
        assert metrics["before"]["badgeText"] == "ASK?"
        assert metrics["before"]["tabAttention"] is True
        assert metrics["before"]["panelNeedsApproval"] is True
        assert "1 ASK?" in metrics["before"]["topbarText"]
        assert metrics["after"]["badgeText"] == ""
        assert metrics["after"]["tabAttention"] is False
        assert metrics["after"]["panelNeedsApproval"] is False
        assert "0 ASK?" in metrics["after"]["topbarText"]
        assert metrics["after"]["newInputFrames"] == 0
    finally:
        if app is not None:
            stop = getattr(getattr(app, "control_server", None), "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass
        subprocess.run(
            [tmux_binary, "-S", str(socket_path), "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        shutil.rmtree(sock_base, ignore_errors=True)
        cleanup_isolated_browser_runtime_paths(paths)


@pytest.mark.e2e
def test_real_agent_prompts_render_ask_attention_in_live_server(browser, monkeypatch, tmp_path):
    if os.environ.get("YOLOMUX_REAL_AGENT_SMOKE") != "1":
        pytest.skip("set YOLOMUX_REAL_AGENT_SMOKE=1 to run real Claude/Codex prompt smoke")
    tmux_binary = shutil.which("tmux")
    codex_binary = shutil.which("codex")
    claude_binary = shutil.which("claude")
    if not tmux_binary:
        pytest.skip("tmux is not installed")
    if not codex_binary:
        pytest.skip("codex is not installed")
    if not claude_binary:
        pytest.skip("claude is not installed")

    paths = isolate_browser_runtime_paths(monkeypatch, tmp_path)
    sock_base = Path("/tmp") / f"yoask-real-ui-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    sock_base.mkdir(mode=0o700)
    socket_path = sock_base / "s"
    sessions = {
        "codex": f"yr-codex-{os.getpid()}-{uuid.uuid4().hex[:6]}",
        "claude": f"yr-claude-{os.getpid()}-{uuid.uuid4().hex[:6]}",
    }
    monkeypatch.setenv(YOLOMUX_TMUX_SOCKET_ENV, str(socket_path))

    def tmux_cmd(*args, timeout=8):
        return subprocess.run(
            [tmux_binary, "-S", str(socket_path), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def capture(session):
        return tmux_cmd("capture-pane", "-p", "-t", f"{session}:").stdout or ""

    def wait_until(session, predicate, timeout=120):
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            last = capture(session)
            if predicate(last):
                return True, last
            time.sleep(0.4)
        return False, last

    def wait_for_codex_sleep_prompt(session, timeout=120):
        deadline = time.time() + timeout
        last = ""
        extra_submit_sent = False
        while time.time() < deadline:
            last = capture(session)
            if "Would you like to run the following" in last and "sleep 10" in last:
                return True, last
            if not extra_submit_sent and "› Run sleep 10" in last:
                tmux_cmd("send-keys", "-t", f"{session}:", "C-m")
                extra_submit_sent = True
            time.sleep(0.4)
        return False, last

    def wait_for_claude_plan_prompt(session, timeout=120):
        deadline = time.time() + timeout
        last = ""
        extra_submit_sent = False
        while time.time() < deadline:
            last = capture(session)
            if "Claude has written up a plan" in last and "Would you like to proceed?" in last:
                return True, last
            if not extra_submit_sent and "Add a temporary line to README.md" in last:
                tmux_cmd("send-keys", "-t", f"{session}:", "C-m")
                extra_submit_sent = True
            time.sleep(0.4)
        return False, last

    app = None
    server = None
    thread = None
    try:
        codex_command = (
            f"cd {REPO_ROOT} && exec codex --no-alt-screen "
            f"--ask-for-approval untrusted --sandbox read-only -C {REPO_ROOT}"
        )
        claude_command = f"cd {REPO_ROOT} && exec claude --permission-mode plan --safe-mode"
        for session, command in ((sessions["codex"], codex_command), (sessions["claude"], claude_command)):
            created = tmux_cmd("new-session", "-d", "-s", session, "-x", "120", "-y", "40", command, timeout=10)
            assert created.returncode == 0, f"tmux new-session failed for {session}: {created.stderr or created.stdout}"

        codex_ready, codex_pane = wait_until(sessions["codex"], lambda text: "›" in text or "Codex" in text, timeout=45)
        assert codex_ready, f"Codex did not reach an input prompt:\n{codex_pane}"
        claude_ready, claude_pane = wait_until(sessions["claude"], lambda text: "❯" in text or "Claude Code" in text, timeout=45)
        assert claude_ready, f"Claude did not reach an input prompt:\n{claude_pane}"

        app = TmuxWebtermApp(list(sessions.values()), dangerously_yolo=False)
        initial_payloads = {
            agent: app.auto_approve_session_status(session, capture_bare_session_when_roster=True)
            for agent, session in sessions.items()
        }
        assert initial_payloads["codex"]["prompt"]["visible"] is False, initial_payloads["codex"]
        assert initial_payloads["claude"]["prompt"]["visible"] is False, initial_payloads["claude"]

        server, thread = start_browser_share_server(monkeypatch, tmp_path, app, auth_bypass=True)
        base_url = f"http://127.0.0.1:{server.server_address[1]}/"
        browser.get(base_url + "?" + urlencode({"sessions": ",".join(sessions.values())}))
        WebDriverWait(browser, 10).until(
            lambda driver: driver.execute_script(
                """
                const sessions = arguments[0];
                return sessions.every(session => document.getElementById(`panel-tab-${session}`))
                  && typeof globalActivityCounts === 'function'
                  && globalActivityCounts().questions === 0;
                """,
                list(sessions.values()),
            )
        )
        initial_ui = browser.execute_script(
            """
            const sessions = arguments[0];
            return {
              questions: globalActivityCounts().questions,
              badges: sessions.map(session => document.getElementById(`panel-tab-${session}`)?.querySelector('[data-prompt-attention-clear]')?.textContent || ''),
              topbar: document.getElementById('topbarActivity')?.textContent || '',
            };
            """,
            list(sessions.values()),
        )
        assert initial_ui["questions"] == 0, initial_ui
        assert initial_ui["badges"] == ["", ""], initial_ui

        tmux_cmd("send-keys", "-t", f"{sessions['codex']}:", "Run sleep 10", "Enter")
        codex_prompted, codex_pane = wait_for_codex_sleep_prompt(sessions["codex"])
        assert codex_prompted, f"Codex did not render the real sleep approval prompt:\n{codex_pane}"

        tmux_cmd("send-keys", "-t", f"{sessions['claude']}:", "Add a temporary line to README.md, then wait for approval before editing", "Enter")
        claude_prompted, claude_pane = wait_for_claude_plan_prompt(sessions["claude"])
        assert claude_prompted, f"Claude did not render the real plan approval prompt:\n{claude_pane}"

        prompted_status_payload, prompted_status = app.auto_approve_status()
        assert prompted_status == HTTPStatus.OK, prompted_status_payload
        prompted_payloads = {
            agent: prompted_status_payload["sessions"][session]
            for agent, session in sessions.items()
        }
        assert prompted_payloads["codex"]["prompt"]["visible"] is True, prompted_payloads["codex"]
        assert prompted_payloads["codex"]["screen"]["key"] == "approval", prompted_payloads["codex"]
        assert prompted_payloads["codex"]["prompt"]["agent"] == "codex", prompted_payloads["codex"]
        assert prompted_payloads["codex"]["prompt"]["prompt_kind"] == "shell-command", prompted_payloads["codex"]
        assert prompted_payloads["claude"]["prompt"]["visible"] is True, prompted_payloads["claude"]
        assert prompted_payloads["claude"]["screen"]["key"] == "approval", prompted_payloads["claude"]
        assert prompted_payloads["claude"]["prompt"]["agent"] == "claude", prompted_payloads["claude"]

        ui_deadline = time.time() + 15
        prompted_ui = {}
        while time.time() < ui_deadline:
            prompted_ui = browser.execute_async_script(
                """
                const sessions = arguments[0];
                const done = arguments[arguments.length - 1];
                Promise.resolve(refreshAutoStatuses()).then(() => {
                  refreshActivePanelHeaders();
                  updateTopbarActivityStatus();
                  const counts = globalActivityCounts();
                  done({
                    ok: counts.questions === sessions.length,
                    counts,
                    topbar: document.getElementById('topbarActivity')?.textContent || '',
                    states: sessions.map(session => {
                      const payload = autoApproveStates.get(session) || {};
                      const state = sessionState(session);
                      const tab = document.getElementById(`panel-tab-${session}`);
                      const panel = document.getElementById(`panel-${session}`);
                      return {
                        session,
                        promptVisible: payload.prompt?.visible === true,
                        promptAgent: payload.prompt?.agent || '',
                        screenKey: payload.screen?.key || '',
                        stateKey: state?.key || '',
                        stateAttention: state?.attention === true,
                        badge: tab?.querySelector('[data-prompt-attention-clear]')?.textContent || '',
                        tabAttention: tab?.classList.contains('needs-attention') || false,
                        panelNeedsApproval: panel?.classList.contains('needs-exec-pane') || false,
                      };
                    }),
                    errors: window.__bootErrors || [],
                    rejections: window.__bootRejections || [],
                  });
                }).catch(error => done({ok: false, error: String(error && error.stack || error)}));
                """,
                list(sessions.values()),
            )
            if prompted_ui.get("ok") is True:
                break
            time.sleep(0.5)
        assert prompted_ui.get("ok") is True, prompted_ui
        browser.execute_script(
            """
            if (!WebSocket.prototype.__askClearTracked) {
              const nativeSend = WebSocket.prototype.send;
              window.__askClearWsFrames = [];
              WebSocket.prototype.send = function(data) {
                window.__askClearWsFrames.push({url: String(this.url || ''), data: String(data || '')});
                return nativeSend.call(this, data);
              };
              WebSocket.prototype.__askClearTracked = true;
            } else {
              window.__askClearWsFrames = [];
            }
            """
        )
        metrics = browser.execute_script(
            """
            const sessions = arguments[0];
            function snapshot() {
              return {
                questions: globalActivityCounts().questions,
                topbar: document.getElementById('topbarActivity')?.textContent || '',
                sessions: sessions.map(session => {
                  const tab = document.getElementById(`panel-tab-${session}`);
                  const panel = document.getElementById(`panel-${session}`);
                  return {
                    session,
                    badge: tab?.querySelector('[data-prompt-attention-clear]')?.textContent || '',
                    tabAttention: tab?.classList.contains('needs-attention') || false,
                    panelNeedsApproval: panel?.classList.contains('needs-exec-pane') || false,
                  };
                }),
              };
            }
            const before = snapshot();
            for (const session of sessions) {
              document.getElementById(`panel-tab-${session}`)?.querySelector('[data-prompt-attention-clear]')?.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
            }
            const after = snapshot();
            const inputFrames = (window.__askClearWsFrames || []).filter(frame => frame.data.includes('"type":"input"'));
            return {before, after, inputFrames};
            """,
            list(sessions.values()),
        )
        assert metrics["before"]["questions"] == 2, metrics
        assert "2 ASK?" in metrics["before"]["topbar"], metrics
        assert [item["badge"] for item in metrics["before"]["sessions"]] == ["ASK?", "ASK?"], metrics
        assert [item["tabAttention"] for item in metrics["before"]["sessions"]] == [True, True], metrics
        assert [item["panelNeedsApproval"] for item in metrics["before"]["sessions"]] == [True, True], metrics
        assert metrics["after"]["questions"] == 0, metrics
        assert "0 ASK?" in metrics["after"]["topbar"], metrics
        assert [item["badge"] for item in metrics["after"]["sessions"]] == ["", ""], metrics
        assert [item["tabAttention"] for item in metrics["after"]["sessions"]] == [False, False], metrics
        assert [item["panelNeedsApproval"] for item in metrics["after"]["sessions"]] == [False, False], metrics
        assert metrics["inputFrames"] == [], metrics
    finally:
        if server is not None and thread is not None:
            stop_browser_share_server(server, thread)
        elif app is not None:
            stop = getattr(getattr(app, "control_server", None), "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass
        subprocess.run(
            [tmux_binary, "-S", str(socket_path), "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        shutil.rmtree(sock_base, ignore_errors=True)
        cleanup_isolated_browser_runtime_paths(paths)


def test_yoagent_settings_operator_updates_live_gui_and_denies_readonly(browser, tmp_path):
    base_settings = {
        "appearance": {
            "theme": "dark",
            "active_color": "green",
            "tab_width": 180,
            "terminal_font_size": 13,
        },
        "updates": {"notify_level": "patch"},
        "yoagent": {"backend": "auto"},
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        sessions=["1"],
        settings=base_settings,
        yoagent_chat_mode="settings",
        available_agents=["term", "codex"],
        agent_auth={"codex": {"installed": True, "logged_in": True}},
    )
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return typeof openInfoSubTab === 'function' && typeof sendYoagentChatMessage === 'function'"))
    admin = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          await openInfoSubTab('yoagent');
          for (const prompt of [
            'set theme to light',
            'set active color to blue',
            'set tab width to 220',
            'set terminal font size to 18',
            'change notification level to none',
            'maybe theme',
          ]) {
            await sendYoagentChatMessage(prompt);
          }
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const rootStyle = getComputedStyle(document.documentElement);
          const chat = document.querySelector('.yoagent-chat');
          const history = document.querySelector('.yoagent-chat-history')?.getBoundingClientRect();
          const form = document.querySelector('[data-yoagent-chat-form]')?.getBoundingClientRect();
          done({
            bodyClass: document.body.className,
            theme: clientSettings.appearance?.theme,
            activeColor: clientSettings.appearance?.active_color,
            activeAccent: rootStyle.getPropertyValue('--active-accent').trim(),
            tabWidth: rootStyle.getPropertyValue('--pane-tab-width').trim(),
            terminalFontSize: rootStyle.getPropertyValue('--terminal-font-size').trim(),
            notifyLevel: clientSettings.updates?.notify_level,
            text: document.querySelector('#yoagent-content')?.innerText || '',
            assistantCount: document.querySelectorAll('.yoagent-message.assistant').length,
            formEnabled: document.querySelector('[data-yoagent-chat-input]')?.disabled === false,
            noOverlap: Boolean(chat && history && form && history.bottom <= form.top + 1),
          });
        })().catch(error => done({error: String(error && error.stack || error)}));
        """
    )
    assert admin.get("error") is None, admin
    assert "theme-light" in admin["bodyClass"]
    assert admin["theme"] == "light"
    assert admin["activeColor"] == "blue"
    assert admin["activeAccent"] == "#2563eb"
    assert admin["tabWidth"] == "220px"
    assert admin["terminalFontSize"] == "18px"
    assert admin["notifyLevel"] == "none"
    assert "Updated this Preference" in admin["text"]
    assert "| `appearance.theme` | `dark` | `light` | Preferences -> Appearance | `live` |" in admin["text"]
    assert "Which setting do you mean" in admin["text"]
    assert admin["assistantCount"] >= 6
    assert admin["formEnabled"] is True
    assert admin["noOverlap"] is True

    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        sessions=["1"],
        settings=base_settings,
        access_role="readonly",
        yoagent_chat_mode="settings",
        available_agents=["term", "codex"],
        agent_auth={"codex": {"installed": True, "logged_in": True}},
    )
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return typeof openInfoSubTab === 'function' && typeof sendYoagentChatMessage === 'function'"))
    readonly = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          await openInfoSubTab('yoagent');
          await sendYoagentChatMessage('set theme to light');
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          done({
            bodyClass: document.body.className,
            theme: clientSettings.appearance?.theme,
            text: document.querySelector('#yoagent-content')?.innerText || '',
            inputDisabled: document.querySelector('[data-yoagent-chat-input]')?.disabled === true,
          });
        })().catch(error => done({error: String(error && error.stack || error)}));
        """
    )
    assert readonly.get("error") is None, readonly
    assert "theme-light" not in readonly["bodyClass"]
    assert readonly["theme"] == "dark"
    assert "requires an admin login" in readonly["text"]
    assert readonly["inputDisabled"] is False


def test_yoagent_busy_chat_uses_one_vertical_scroll_owner(browser, tmp_path):
    for label, grid_width, window_width in (("desktop", 1000, 1120), ("narrow", 520, 640)):
        browser.set_window_size(window_width, 720)
        load_live_runtime_boot_fixture(
            browser,
            tmp_path,
            sessions=["1"],
            settings={"yoagent": {"backend": "auto"}},
            grid_width=grid_width,
            grid_height=460,
            available_agents=["term", "codex"],
            agent_auth={"codex": {"installed": True, "logged_in": True}},
        )
        WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return typeof openInfoSubTab === 'function' && typeof sendYoagentChatMessage === 'function'"))
        metrics = browser.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            const raf = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const responsePayload = {
              answer: 'done',
              backend: 'yolomux',
              backend_used: 'yolomux',
              deterministic: true,
              conversation: {
                messages: [
                  {role: 'user', content: 'summarize activity', createdAt: '2026-06-19T08:00:00Z'},
                  {role: 'assistant', content: 'Done.', createdAt: '2026-06-19T08:00:01Z'},
                ],
                pending_waits: [],
              },
            };
            const rectFor = element => {
              if (!element) return null;
              const rect = element.getBoundingClientRect();
              return {
                left: rect.left,
                top: rect.top,
                right: rect.right,
                bottom: rect.bottom,
                width: rect.width,
                height: rect.height,
              };
            };
            const boxFor = element => {
              if (!element) return null;
              const style = getComputedStyle(element);
              return {
                overflowX: style.overflowX,
                overflowY: style.overflowY,
                overscrollBehaviorX: style.overscrollBehaviorX,
                overscrollBehaviorY: style.overscrollBehaviorY,
                scrollHeight: element.scrollHeight,
                clientHeight: element.clientHeight,
                scrollWidth: element.scrollWidth,
                clientWidth: element.clientWidth,
                scrollTop: element.scrollTop,
                rect: rectFor(element),
              };
            };
            const styleFor = element => {
              if (!element) return null;
              const style = getComputedStyle(element);
              return {
                overflowX: style.overflowX,
                overflowY: style.overflowY,
                overscrollBehaviorX: style.overscrollBehaviorX,
                overscrollBehaviorY: style.overscrollBehaviorY,
                pointerEvents: style.pointerEvents,
              };
            };
            const collect = () => {
              const infoPane = document.querySelector('.info-pane');
              const outer = document.querySelector('#yoagent-content.info-list.yoagent-list');
              const chat = document.querySelector('.yoagent-chat');
              const history = document.querySelector('.yoagent-chat-history');
              const form = document.querySelector('[data-yoagent-chat-form]');
              const status = document.querySelector('.yoagent-chat-status');
              const streaming = document.querySelector('.yoagent-message.streaming');
              const pre = document.querySelector('.yoagent-chat .markdown-body pre');
              const details = document.querySelector('.yoagent-message-details:not(.yoagent-toolcall-details)');
              const toolDetails = document.querySelector('.yoagent-toolcall-details');
              const auxPreview = details?.querySelector('.yoagent-details-preview');
              const auxStream = details?.querySelector('.yoagent-auxiliary-stream');
              const toolPreview = toolDetails?.querySelector('.yoagent-details-preview');
              const toolStream = toolDetails?.querySelector('.yoagent-toolcall-stream');
              const assistantMessage = document.querySelector('.yoagent-message.assistant');
              const userMessage = document.querySelector('.yoagent-message.user');
              const messageBody = document.querySelector('.yoagent-message-body');
              const markdownBody = document.querySelector('.yoagent-message-body.markdown-body');
              const streamingBody = streaming?.querySelector?.('.yoagent-message-body');
              const actionText = document.querySelector('.yoagent-action-text');
              const input = document.querySelector('[data-yoagent-chat-input]');
              const stopButton = document.querySelector('[data-yoagent-chat-cancel]');
              const queue = document.querySelector('.yoagent-chat-queue');
              const queueCancel = document.querySelector('[data-yoagent-queued-cancel]');
              const boxes = {
                infoPane: boxFor(infoPane),
                outer: boxFor(outer),
                chat: boxFor(chat),
                history: boxFor(history),
                pre: boxFor(pre),
                details: boxFor(details),
                actionText: boxFor(actionText),
              };
              const targetStyles = {
                assistantMessage: styleFor(assistantMessage),
                userMessage: styleFor(userMessage),
                messageBody: styleFor(messageBody),
                markdownBody: styleFor(markdownBody),
                auxPreview: styleFor(auxPreview),
                auxStream: styleFor(auxStream),
              };
              return {
                ...boxes,
                targetStyles,
                verticalOverflowKeys: Object.entries(boxes)
                  .filter(([, box]) => box && box.scrollHeight > box.clientHeight + 1)
                  .map(([key]) => key),
                hasStatus: Boolean(status),
                hasStreaming: Boolean(streaming),
                noHistoryFormOverlap: Boolean(history && form && history.getBoundingClientRect().bottom <= form.getBoundingClientRect().top + 1),
                statusInsideHistory: Boolean(status && history && history.contains(status)),
                streamingInsideHistory: Boolean(streaming && history && history.contains(streaming)),
                inputDisabled: input?.disabled === true,
                hasStopButton: Boolean(stopButton),
                hasQueue: Boolean(queue),
                queueText: queue?.textContent || '',
                hasQueuedCancel: Boolean(queueCancel),
                thinkingDetailsOpen: details?.open === true,
                toolDetailsOpen: toolDetails?.open === true,
                auxPreviewText: auxPreview?.textContent || '',
                auxStreamText: auxStream?.textContent || '',
                toolPreviewText: toolPreview?.textContent || '',
                toolStreamText: toolStream?.textContent || '',
                streamingBodyText: streamingBody?.textContent || '',
                errors: window.__bootErrors || [],
                rejections: window.__bootRejections || [],
              };
            };
            (async () => {
              await openInfoSubTab('yoagent');
              applyYoagentConversationPayload({
                transcript_path: '/home/test/.local/state/yolomux/yoagent/conversation.jsonl',
                transcript_display_path: '~/.local/state/yolomux/yoagent/conversation.jsonl',
                messages: Array.from({length: 80}, (_, index) => ({
                  role: index % 2 ? 'assistant' : 'user',
                  content: index === 1
                    ? 'message 2 with a wide code block\\n\\n```js\\nconst wide = "' + Array.from({length: 36}, () => 'wide_token').join('_') + '";\\n```'
                    : `message ${index + 1} ` + Array.from({length: 10}, (_line, lineIndex) => `detail-${lineIndex + 1}-for-message-${index + 1}`).join(' '),
                  createdAt: `2026-06-19T08:${String(index).padStart(2, '0')}:00Z`,
                })),
                pending_waits: [{id: 'wait-1', session: '1', started_ts: Math.round(Date.now() / 1000) - 45, transcript: '/tmp/yolomux-transcript.jsonl'}],
              });
              renderYoagentPanel({scrollBottom: true});
              await raf();
              const originalFetch = window.fetch;
              let releaseChat = null;
              const fetchCalls = [];
              window.fetch = (input, options = {}) => {
                const url = new URL(String(input), window.location.href);
                fetchCalls.push({path: url.pathname, method: options.method || 'GET', body: String(options.body || ''), hasSignal: Boolean(options.signal)});
                if (url.pathname === '/api/yoagent/chat') {
                  return new Promise((resolve, reject) => {
                    releaseChat = () => resolve(new Response(JSON.stringify(responsePayload), {status: 200, headers: {'Content-Type': 'application/json'}}));
                    options.signal?.addEventListener?.('abort', () => {
                      const error = new Error('aborted');
                      error.name = 'AbortError';
                      reject(error);
                    });
                  });
                }
                if (/^\/api\/yoagent\/chat\/.+\/cancel$/.test(url.pathname)) {
                  return Promise.resolve(new Response(JSON.stringify({ok: true, cancelled: true}), {status: 200, headers: {'Content-Type': 'application/json'}}));
                }
                return originalFetch(input, options);
              };
              const sendPromise = sendYoagentChatMessage('summarize activity');
              await raf();
              const active = yoagentActiveChatRequest ? {id: yoagentActiveChatRequest.id, streamId: yoagentActiveChatRequest.streamId} : null;
              const immediate = collect();
              const input = document.querySelector('[data-yoagent-chat-input]');
              const form = document.querySelector('[data-yoagent-chat-form]');
              if (input && form) {
                input.value = 'queued followup';
                input.dispatchEvent(new Event('input', {bubbles: true}));
                form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
              }
              await raf();
              const queueBeforeCancel = collect();
              document.querySelector('[data-yoagent-queued-cancel]')?.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
              await raf();
              const queueAfterCancel = collect();
              applyYoagentStreamPayload({
                stream_id: active?.streamId || active?.id || '',
                backend: 'claude',
                phase: 'delta',
                content: Array.from({length: 24}, (_, index) => `streamed line ${index + 1}: working through the activity context`).join('\\n'),
                auxiliary_lines: ['thinking: scanning recent events', 'thinking: reading activity context', 'thinking: final synthesis', 'tool output: command: collected files'],
                auxiliary_preview: 'thinking: reading activity context\\nthinking: final synthesis\\ntool output: command: collected files',
                hidden_work_active: true,
                tool_active: true,
              });
              renderYoagentPanel({preserveDraft: true, scrollBottom: 'auto', allowBusyRebuild: true});
              await raf();
              await new Promise(resolve => setTimeout(resolve, 25));
              const history = document.querySelector('.yoagent-chat-history');
              if (history) history.scrollTop = 1;
              const manualScrollTop = history?.scrollTop || 0;
              renderYoagentPanel({preserveDraft: true, scrollBottom: 'auto'});
              await raf();
              const measured = collect();
              const thinkingDetails = document.querySelector('.yoagent-message-details:not(.yoagent-toolcall-details)');
              const toolDetails = document.querySelector('.yoagent-toolcall-details');
              const collapsedThinkingHeight = thinkingDetails?.getBoundingClientRect?.().height || 0;
              const collapsedToolHeight = toolDetails?.getBoundingClientRect?.().height || 0;
              if (thinkingDetails) thinkingDetails.open = true;
              if (toolDetails) toolDetails.open = true;
              await raf();
              measured.expandedThinkingStreamText = thinkingDetails?.querySelector('.yoagent-auxiliary-stream')?.textContent || '';
              measured.expandedToolStreamText = toolDetails?.querySelector('.yoagent-toolcall-stream')?.textContent || '';
              measured.expandedThinkingHeight = thinkingDetails?.getBoundingClientRect?.().height || 0;
              measured.expandedToolHeight = toolDetails?.getBoundingClientRect?.().height || 0;
              measured.collapsedThinkingHeight = collapsedThinkingHeight;
              measured.collapsedToolHeight = collapsedToolHeight;
              measured.manualScrollTop = manualScrollTop;
              const refreshedHistory = document.querySelector('.yoagent-chat-history');
              measured.afterRefreshScrollTop = refreshedHistory?.scrollTop || 0;
              measured.afterRefreshBottomGap = refreshedHistory ? refreshedHistory.scrollHeight - refreshedHistory.clientHeight - refreshedHistory.scrollTop : 0;
              measured.releaseAvailable = typeof releaseChat === 'function';
              measured.activeRequest = active;
              measured.immediate = immediate;
              measured.queueBeforeCancel = queueBeforeCancel;
              measured.queueAfterCancel = queueAfterCancel;
              document.querySelector('[data-yoagent-chat-cancel]')?.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
              await sendPromise;
              await raf();
              measured.afterCancel = {
                activeRequest: yoagentActiveChatRequest ? {id: yoagentActiveChatRequest.id, streamId: yoagentActiveChatRequest.streamId} : null,
                busy: yoagentBusy === true,
                inputDisabled: document.querySelector('[data-yoagent-chat-input]')?.disabled === true,
                stoppedText: document.querySelector('.yoagent-message.stopped')?.textContent || '',
                cancelCallCount: fetchCalls.filter(call => /^\\/api\\/yoagent\\/chat\\/.+\\/cancel$/.test(call.path)).length,
              };
              measured.chatRequestBodies = fetchCalls.filter(call => call.path === '/api/yoagent/chat').map(call => JSON.parse(call.body || '{}'));
              window.fetch = originalFetch;
              done(measured);
            })().catch(error => done({error: String(error && error.stack || error), errors: window.__bootErrors || [], rejections: window.__bootRejections || []}));
            """
        )
        assert metrics.get("error") is None, (label, metrics)
        assert metrics["errors"] == [], (label, metrics)
        assert metrics["rejections"] == [], (label, metrics)
        assert metrics["releaseAvailable"] is True, (label, metrics)
        assert metrics["activeRequest"]["id"] and metrics["activeRequest"]["streamId"], (label, metrics)
        assert metrics["chatRequestBodies"][0]["request_id"] == metrics["activeRequest"]["id"], (label, metrics)
        assert metrics["chatRequestBodies"][0]["stream_id"] == metrics["activeRequest"]["streamId"], (label, metrics)
        assert metrics["immediate"]["inputDisabled"] is False, (label, metrics)
        assert metrics["immediate"]["hasStopButton"] is True, (label, metrics)
        assert "thinking" in metrics["immediate"]["auxPreviewText"], (label, metrics)
        assert metrics["queueBeforeCancel"]["hasQueue"] is True, (label, metrics)
        assert metrics["queueBeforeCancel"]["hasQueuedCancel"] is True, (label, metrics)
        assert "queued followup" in metrics["queueBeforeCancel"]["queueText"], (label, metrics)
        assert metrics["queueAfterCancel"]["hasQueue"] is False, (label, metrics)
        assert metrics["hasStatus"] is True, (label, metrics)
        assert metrics["hasStreaming"] is True, (label, metrics)
        assert metrics["statusInsideHistory"] is True, (label, metrics)
        assert metrics["streamingInsideHistory"] is True, (label, metrics)
        assert metrics["hasStopButton"] is True, (label, metrics)
        assert metrics["inputDisabled"] is False, (label, metrics)
        assert metrics["thinkingDetailsOpen"] is False, (label, metrics)
        assert metrics["toolDetailsOpen"] is False, (label, metrics)
        assert metrics["auxPreviewText"] == "thinking: reading activity context\nthinking: final synthesis", (label, metrics)
        assert metrics["toolPreviewText"] == "tool output: command: collected files", (label, metrics)
        assert "thinking: reading activity context" in metrics["auxStreamText"], (label, metrics)
        assert "tool output: command: collected files" not in metrics["auxStreamText"], (label, metrics)
        assert "tool output: command: collected files" in metrics["toolStreamText"], (label, metrics)
        assert "thinking: scanning recent events" in metrics["expandedThinkingStreamText"], (label, metrics)
        assert "tool output: command: collected files" in metrics["expandedToolStreamText"], (label, metrics)
        assert metrics["expandedThinkingHeight"] > metrics["collapsedThinkingHeight"], (label, metrics)
        assert metrics["expandedToolHeight"] > metrics["collapsedToolHeight"], (label, metrics)
        assert "thinking: reading activity context" not in metrics["streamingBodyText"], (label, metrics)
        assert metrics["afterCancel"]["activeRequest"] is None, (label, metrics)
        assert metrics["afterCancel"]["busy"] is False, (label, metrics)
        assert metrics["afterCancel"]["inputDisabled"] is False, (label, metrics)
        assert metrics["afterCancel"]["cancelCallCount"] == 1, (label, metrics)
        assert "Stopped." in metrics["afterCancel"]["stoppedText"], (label, metrics)
        assert metrics["outer"]["overflowY"] == "hidden", (label, metrics)
        assert metrics["outer"]["scrollHeight"] <= metrics["outer"]["clientHeight"] + 1, (label, metrics)
        assert metrics["history"]["overflowY"] == "auto", (label, metrics)
        assert metrics["history"]["overscrollBehaviorY"] == "auto", (label, metrics)
        assert metrics["history"]["scrollHeight"] > metrics["history"]["clientHeight"], (label, metrics)
        assert metrics["verticalOverflowKeys"] == ["history"], (label, metrics)
        for target in ("assistantMessage", "userMessage", "messageBody", "markdownBody"):
            assert metrics["targetStyles"][target]["overflowY"] == "visible", (label, target, metrics)
            assert metrics["targetStyles"][target]["overscrollBehaviorY"] == "auto", (label, target, metrics)
        assert metrics["targetStyles"]["auxPreview"]["overflowY"] == "clip", (label, metrics)
        assert metrics["targetStyles"]["auxStream"]["overscrollBehaviorY"] == "auto", (label, metrics)
        assert metrics["chat"]["rect"]["bottom"] <= metrics["outer"]["rect"]["bottom"] + 1, (label, metrics)
        assert metrics["noHistoryFormOverlap"] is True, (label, metrics)
        assert metrics["manualScrollTop"] > 0, (label, metrics)
        assert metrics["afterRefreshScrollTop"] > 0, (label, metrics)
        assert metrics["afterRefreshBottomGap"] > 48, (label, metrics)
        screenshot = browser_screenshot_rgb(browser)
        assert screenshot.size[0] >= window_width - 20, (label, screenshot.size)
        assert screenshot.getbbox() is not None, label


def test_yoagent_auxiliary_details_are_subdued_in_dark_and_light(browser, tmp_path):
    for theme_class in ("theme-dark", "theme-light"):
        page = tmp_path / f"yoagent-auxiliary-{theme_class}.html"
        page.write_text(
            page_html(
                f"""
                <script>document.body.className = {json.dumps(theme_class)};</script>
                <section class="yoagent-chat">
                  <div class="yoagent-message assistant">
                    <div class="yoagent-message-role"><span>YO!agent</span></div>
                    <div class="yoagent-message-body markdown-body">Normal assistant answer</div>
                    <details class="yoagent-message-details has-auxiliary" open>
                      <summary><span>Details</span><span class="yoagent-details-preview">thinking: preview</span></summary>
                      <pre class="yoagent-auxiliary-stream">thinking: preview\ntool output: done</pre>
                    </details>
                  </div>
                </section>
                """,
                extra_css="body { margin: 0; padding: 20px; background: var(--bg); color: var(--text); } .yoagent-chat { width: 420px; }",
            ),
            encoding="utf-8",
        )
        browser.get(page.as_uri())
        metrics = browser.execute_script(
            """
            const body = document.querySelector('.yoagent-message-body');
            const aux = document.querySelector('.yoagent-auxiliary-stream');
            const preview = document.querySelector('.yoagent-details-preview');
            return {
              bodyColor: getComputedStyle(body).color,
              auxColor: getComputedStyle(aux).color,
              previewColor: getComputedStyle(preview).color,
              auxText: aux.textContent,
            };
            """
        )
        assert metrics["auxText"] == "thinking: preview\ntool output: done", (theme_class, metrics)
        assert metrics["auxColor"] != metrics["bodyColor"], (theme_class, metrics)
        assert metrics["previewColor"] != metrics["bodyColor"], (theme_class, metrics)


def test_tabber_session_rows_use_pane_tab_shape_and_keep_columns(browser, tmp_path):
    for label, theme_class, pane_width, window_width in (
        ("dark-narrow", "theme-dark", 300, 700),
        ("light-wide", "theme-light", 520, 900),
    ):
        browser.set_window_size(window_width, 720)
        page = tmp_path / f"tabber-session-row-{label}.html"
        page.write_text(
            page_html(
                f"""
                <script>document.body.className = {json.dumps(theme_class)};</script>
                <section class="fixture-tabber-panel file-explorer-changes-panel">
                  <div class="file-tree" role="tree">
                    <div class="file-tree-row tabber-row kind-dir expanded tabber-active-session" data-tabber-type="session" data-tabber-session="1" role="treeitem" aria-expanded="true" aria-selected="false" aria-current="true" style="padding-left: 8px;">
                      <span class="file-tree-icon tabber-icon">▾</span>
                      <span class="file-tree-name"><span class="tabber-session-tab active"><span class="tabber-session-name">8801</span><span class="tabber-session-description">tabber session tab styling keeps the date column visible for a deliberately long work description</span></span></span>
                      <span class="file-tree-agent" hidden></span>
                      <span class="file-tree-diff" hidden></span>
                      <span class="file-tree-dir-count" hidden></span>
                      <span class="file-tree-git-status" hidden></span>
                      <span class="file-tree-date">2m ago</span>
                    </div>
                    <div class="file-tree-row tabber-row kind-file" data-tabber-type="window" data-tabber-session="1" role="treeitem" aria-selected="false" style="padding-left: 27px;">
                      <span class="file-tree-icon tabber-icon">⌁</span>
                      <span class="file-tree-name"><span class="tabber-window-label"><span class="tabber-window-text">0:bash</span></span></span>
                      <span class="file-tree-agent" hidden></span>
                      <span class="file-tree-diff" hidden></span>
                      <span class="file-tree-dir-count" hidden></span>
                      <span class="file-tree-git-status" hidden></span>
                      <span class="file-tree-date">2m ago</span>
                    </div>
                    <div class="file-tree-row tabber-row kind-dir expanded" data-tabber-type="session" data-tabber-session="2" role="treeitem" aria-expanded="true" aria-selected="false" style="padding-left: 8px;">
                      <span class="file-tree-icon tabber-icon">▾</span>
                      <span class="file-tree-name"><span class="tabber-session-tab"><span class="tabber-session-name">2</span><span class="tabber-session-description">main</span></span></span>
                      <span class="file-tree-agent" hidden></span>
                      <span class="file-tree-diff" hidden></span>
                      <span class="file-tree-dir-count" hidden></span>
                      <span class="file-tree-git-status" hidden></span>
                      <span class="file-tree-date">15m ago</span>
                    </div>
                    <div class="file-tree-row tabber-row kind-file" data-tabber-type="window" data-tabber-session="2" role="treeitem" aria-selected="false" style="padding-left: 27px;">
                      <span class="file-tree-icon tabber-icon">⌁</span>
                      <span class="file-tree-name"><span class="tabber-window-label"><span class="tabber-window-text">0:bash</span></span></span>
                      <span class="file-tree-agent" hidden></span>
                      <span class="file-tree-diff" hidden></span>
                      <span class="file-tree-dir-count" hidden></span>
                      <span class="file-tree-git-status" hidden></span>
                      <span class="file-tree-date">15m ago</span>
                    </div>
                  </div>
                </section>
                """,
                extra_css=f"""
                  body {{ margin: 0; padding: 16px; background: var(--bg); color: var(--text); }}
                  .fixture-tabber-panel {{ width: {pane_width}px; border: 1px solid var(--border); }}
                """,
            ),
            encoding="utf-8",
        )
        browser.get(page.as_uri())
        metrics = browser.execute_script(
            """
            const rectFor = element => {
              if (!element) return null;
              const rect = element.getBoundingClientRect();
              return {
                left: rect.left,
                top: rect.top,
                right: rect.right,
                bottom: rect.bottom,
                width: rect.width,
                height: rect.height,
              };
            };
            const resolvedColor = (scope, value) => {
              const probe = document.createElement('span');
              probe.style.position = 'absolute';
              probe.style.pointerEvents = 'none';
              probe.style.background = value;
              (scope || document.body).appendChild(probe);
              const color = getComputedStyle(probe).backgroundColor;
              probe.remove();
              return color;
            };
            const rowMetrics = row => {
              const tab = row?.querySelector('.tabber-session-tab');
              const name = row?.querySelector('.tabber-session-name');
              const description = row?.querySelector('.tabber-session-description');
              const icon = row?.querySelector(':scope > .file-tree-icon');
              const date = row?.querySelector(':scope > .file-tree-date');
              const style = tab ? getComputedStyle(tab) : null;
              return {
                row: rectFor(row),
                tab: rectFor(tab),
                name: rectFor(name),
                description: rectFor(description),
                icon: rectFor(icon),
                date: rectFor(date),
                tabClass: tab?.className || '',
                rowClass: row?.className || '',
                ariaCurrent: row?.getAttribute('aria-current') || '',
                ariaExpanded: row?.getAttribute('aria-expanded') || '',
                iconText: icon?.textContent || '',
                dateText: (date?.textContent || '').trim(),
                dateDisplay: date ? getComputedStyle(date).display : '',
                dateWidth: date ? date.getBoundingClientRect().width : 0,
                tabBg: style?.backgroundColor || '',
                tabColor: style?.color || '',
                tabHeight: style?.height || '',
                tabRadius: style?.borderTopLeftRadius || '',
                tabBorderTop: style?.borderTopColor || '',
                expectedActiveBg: tab ? resolvedColor(tab.parentElement, 'var(--pane-tab-active-bg)') : '',
                expectedInactiveBg: tab ? resolvedColor(tab.parentElement, 'var(--pane-bar-bg, var(--panel2))') : '',
                descriptionScrollWidth: description?.scrollWidth || 0,
                descriptionClientWidth: description?.clientWidth || 0,
              };
            };
            const sessionRows = Array.from(document.querySelectorAll('.file-tree-row[data-tabber-type="session"]'));
            const windowRows = Array.from(document.querySelectorAll('.file-tree-row[data-tabber-type="window"]'));
            const activeRow = sessionRows.find(row => row.dataset.tabberSession === '1');
            const inactiveRow = sessionRows.find(row => row.dataset.tabberSession === '2');
            const activeWindowRow = windowRows.find(row => row.dataset.tabberSession === '1');
            const windowIcons = windowRows.map(row => (row.querySelector('.file-tree-icon')?.textContent || '').trim());
            const nonSessionWithSessionTab = Array.from(document.querySelectorAll('.file-tree-row:not([data-tabber-type="session"]) .tabber-session-tab')).length;
            return {
              active: rowMetrics(activeRow),
              inactive: rowMetrics(inactiveRow),
              activeWindow: rectFor(activeWindowRow),
              windowIcons,
              nonSessionWithSessionTab,
              sessionCount: sessionRows.length,
              windowCount: windowRows.length,
              bodyClass: document.body.className,
            };
            """
        )
        assert metrics["sessionCount"] >= 2, (label, metrics)
        assert metrics["windowCount"] >= 2, (label, metrics)
        assert metrics["windowIcons"] == ["⌁", "⌁"], (label, metrics)
        assert metrics["nonSessionWithSessionTab"] == 0, (label, metrics)
        assert "tabber-active-session" in metrics["active"]["rowClass"], (label, metrics)
        assert "active" in metrics["active"]["tabClass"], (label, metrics)
        assert metrics["active"]["ariaCurrent"] == "true", (label, metrics)
        assert metrics["active"]["ariaExpanded"] == "true", (label, metrics)
        assert metrics["active"]["iconText"] == "▾", (label, metrics)
        assert "tabber-active-session" not in metrics["inactive"]["rowClass"], (label, metrics)
        assert "active" not in metrics["inactive"]["tabClass"], (label, metrics)
        assert metrics["inactive"]["ariaCurrent"] == "", (label, metrics)
        assert metrics["active"]["tabBg"] == metrics["active"]["expectedActiveBg"], (label, metrics)
        assert metrics["inactive"]["tabBg"] == metrics["inactive"]["expectedInactiveBg"], (label, metrics)
        assert metrics["active"]["tabBg"] != metrics["active"]["tabColor"], (label, metrics)
        assert metrics["inactive"]["tabBg"] != metrics["inactive"]["tabColor"], (label, metrics)
        assert metrics["active"]["tab"]["height"] >= 16, (label, metrics)
        assert metrics["active"]["tabRadius"] == "6px", (label, metrics)
        assert metrics["active"]["dateDisplay"] != "none", (label, metrics)
        assert metrics["active"]["dateWidth"] > 0, (label, metrics)
        assert metrics["active"]["dateText"], (label, metrics)
        assert metrics["active"]["icon"]["right"] <= metrics["active"]["tab"]["left"] + 1, (label, metrics)
        assert metrics["active"]["tab"]["right"] <= metrics["active"]["date"]["left"] + 1, (label, metrics)
        assert metrics["active"]["name"]["left"] >= metrics["active"]["tab"]["left"] - 1, (label, metrics)
        assert metrics["active"]["description"]["right"] <= metrics["active"]["tab"]["right"] + 1, (label, metrics)
        assert metrics["active"]["descriptionScrollWidth"] >= metrics["active"]["descriptionClientWidth"], (label, metrics)
        assert metrics["activeWindow"]["top"] >= metrics["active"]["row"]["bottom"] - 1, (label, metrics)
        screenshot = browser_screenshot_rgb(browser)
        assert screenshot.size[0] >= window_width - 20, (label, screenshot.size)
        assert screenshot.getbbox() is not None, label


def test_generated_app_boots_live_runtime_without_browser_errors(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return window.__terminalOpened >= 1 && document.querySelector('#panel-1 .terminal .xterm') !== null"
        )
    )
    metrics = browser.execute_script(
        """
        return {
          errors: window.__bootErrors,
          rejections: window.__bootRejections,
          fetchPaths: window.__bootFetches.map(item => `${item.method} ${item.path}`),
          sockets: window.__bootSockets,
          menuButtons: Array.from(document.querySelectorAll('.app-menu')).map(menu => {
            const button = menu.querySelector(':scope > .app-menu-button');
            const badge = button?.querySelector('.app-menu-button-badge');
            const label = Array.from(button?.childNodes || [])
              .filter(node => node.nodeType === Node.TEXT_NODE)
              .map(node => node.textContent)
              .join('')
              .trim();
            return {label, badge: badge?.textContent.trim() || ''};
          }),
          panelCount: document.querySelectorAll('.panel').length,
          paneTabCount: document.querySelectorAll('.pane-tab').length,
          panelVisible: document.querySelector('#panel-1')?.isConnected === true,
          status: document.getElementById('status').textContent,
          terminalText: document.querySelector('#panel-1 .terminal .xterm')?.textContent || '',
        };
        """
    )
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert "GET /api/notify" in metrics["fetchPaths"]
    assert "GET /api/auto-approve" in metrics["fetchPaths"]
    assert "POST /api/ensure-session" in metrics["fetchPaths"]
    assert "GET /api/transcripts" in metrics["fetchPaths"]
    assert "GET /api/ping" in metrics["fetchPaths"]
    assert any("/ws?session=1" in url for url in metrics["sockets"])
    assert {"File", "View", "tmux", "Tabs", "Help"}.issubset(
        {button["label"] for button in metrics["menuButtons"]}
    )
    assert any(
        button["label"] == "Tabs" and button["badge"] == "0"
        for button in metrics["menuButtons"]
    )
    assert metrics["panelCount"] >= 1
    assert metrics["paneTabCount"] >= 1
    assert metrics["panelVisible"]
    assert metrics["terminalText"] == "fake terminal"


def test_terminal_visible_selection_cleanup_clears_browser_and_xterm_state(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof clearTerminalVisibleSelection === 'function'
              && typeof terminalVisibleSelectionState === 'function'
              && terminals.get('1')?.term
              && document.querySelector('#term-1 .xterm');
            """
        )
    )
    metrics = browser.execute_script(
        """
        const container = document.getElementById('term-1');
        const xterm = container.querySelector('.xterm');
        xterm.textContent = 'browser selected terminal text';
        const range = document.createRange();
        range.selectNodeContents(xterm);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        const item = terminals.get('1');
        window.__xtermClearCount = 0;
        item.term.getSelection = () => 'xterm selected terminal text';
        item.term.clearSelection = () => { window.__xtermClearCount += 1; };
        rememberTerminalAppClipboardText('1', 'osc52 terminal text');
        const before = terminalVisibleSelectionState('1', item.term, container);
        const result = clearTerminalVisibleSelection('1', item.term, container, 'selenium-test');
        const after = terminalVisibleSelectionState('1', item.term, container);
        return {
          before,
          result,
          after,
          browserSelection: window.getSelection().toString(),
          xtermClearCount: window.__xtermClearCount,
        };
        """
    )
    assert metrics["before"]["browserChars"] == len("browser selected terminal text")
    assert metrics["before"]["xtermChars"] == len("xterm selected terminal text")
    assert metrics["before"]["recentOsc52Chars"] == len("osc52 terminal text")
    assert metrics["result"]["browserCleared"] is True
    assert metrics["browserSelection"] == ""
    assert metrics["xtermClearCount"] == 1
    assert metrics["after"]["browserChars"] == 0


def test_live_app_menu_dropdowns_open_switch_and_expose_hover_state(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return window.__terminalOpened >= 1 && document.querySelectorAll('.app-menu-button').length >= 5"
        )
    )

    def menu_metrics(menu_id):
        return browser.execute_script(
            """
            const menuId = arguments[0];
            const wrapper = document.querySelector(`.app-menu[data-app-menu="${menuId}"]`);
            const button = wrapper?.querySelector?.(':scope > .app-menu-button');
            const popover = wrapper?.querySelector?.(':scope > .app-menu-popover');
            const rect = popover?.getBoundingClientRect?.();
            const style = popover ? getComputedStyle(popover) : null;
            const commands = Array.from(popover?.querySelectorAll?.('.app-menu-command') || []);
            const activeCommands = commands.filter(command => command.classList.contains('share-mirror-active'));
            const openIds = Array.from(document.querySelectorAll('.app-menu.open')).map(menu => menu.dataset.appMenu || '');
            return {
              exists: Boolean(wrapper && button && popover),
              open: wrapper?.classList?.contains('open') || false,
              openIds,
              expanded: button?.getAttribute?.('aria-expanded') || '',
              visible: Boolean(popover && wrapper?.classList?.contains('open') && style.display !== 'none' && style.visibility !== 'hidden' && Number.parseFloat(style.opacity || '1') > 0.9 && rect.width > 20 && rect.height > 20),
              rect: rect ? {left: Math.round(rect.left), top: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height)} : null,
              commandCount: commands.length,
              activeCommandCount: activeCommands.length,
              firstCommand: commands[0]?.textContent?.replace(/\\s+/g, ' ').trim() || '',
              errors: window.__bootErrors || [],
              rejections: window.__bootRejections || [],
            };
            """,
            menu_id,
        )

    for menu_id in ["file", "view", "tmux", "tabs", "help"]:
        browser.find_element("css selector", f'.app-menu[data-app-menu="{menu_id}"] > .app-menu-button').click()
        metrics = WebDriverWait(browser, 5).until(
            lambda _driver: (state if (state := menu_metrics(menu_id))["visible"] and state["commandCount"] > 0 else False)
        )
        assert metrics["exists"] is True, metrics
        assert metrics["open"] is True, metrics
        assert metrics["openIds"] == [menu_id], metrics
        assert metrics["expanded"] == "true", metrics
        assert metrics["rect"]["width"] >= 80, metrics
        assert metrics["rect"]["height"] >= 24, metrics
        assert metrics["firstCommand"], metrics
        assert metrics["errors"] == [], metrics
        assert metrics["rejections"] == [], metrics

        first_command = browser.find_element("css selector", f'.app-menu[data-app-menu="{menu_id}"] > .app-menu-popover .app-menu-command:not([disabled])')
        ActionChains(browser).move_to_element(first_command).perform()
        hover = WebDriverWait(browser, 5).until(
            lambda _driver: (state if (state := menu_metrics(menu_id))["activeCommandCount"] >= 1 else False)
        )
        assert hover["activeCommandCount"] >= 1, hover

    browser.find_element("css selector", '.app-menu[data-app-menu="file"] > .app-menu-button').click()
    ActionChains(browser).move_to_element(browser.find_element("css selector", '.app-menu[data-app-menu="view"] > .app-menu-button')).perform()
    switched = WebDriverWait(browser, 5).until(
        lambda _driver: (state if (state := menu_metrics("view"))["visible"] else False)
    )
    assert switched["openIds"] == ["view"], switched

    browser.find_element("css selector", "#panel-1").click()
    closed = WebDriverWait(browser, 5).until(
        lambda _driver: (state if not (state := menu_metrics("view"))["open"] else False)
    )
    assert closed["openIds"] == [], closed


def test_client_events_ready_refetches_yolo_marker_after_reconnect(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        sessions=["1"],
        auto_approve_payload={
            "session_order": ["1"],
            "sessions": {"1": {"target": "1", "enabled": False, "last_action": "off", "screen": {"key": "idle"}}},
            "rules": {"path": "/home/test/.config/yolomux/yolo-rules.yaml", "source": "default", "rules": [], "errors": []},
        },
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return window.__terminalOpened >= 1 && document.querySelector('[data-yolo-session=\"1\"]') !== null"
        )
    )
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const markerBefore = document.querySelector('[data-yolo-session="1"]');
          const source = (window.__eventSources || []).find(item => item.url === '/api/client-events');
          if (!markerBefore || !source) return {error: 'missing marker or client-events source'};
          const beforeWorking = markerBefore.classList.contains('working');
          window.__fixtureAutoApprovePayload = {
            session_order: ['1'],
            sessions: {'1': {target: '1', enabled: false, last_action: 'off', screen: {key: 'working'}}},
            rules: {path: '/home/test/.config/yolomux/yolo-rules.yaml', source: 'default', rules: [], errors: []},
          };
          clientEventsConnected = false;
          source.emit('ready');
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 90; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => document.querySelector('[data-yolo-session="1"]')?.classList.contains('working'));
          const markerAfter = document.querySelector('[data-yolo-session="1"]');
          return {
            beforeWorking,
            ready,
            connected: clientEventsConnected,
            className: markerAfter?.className || '',
            autoApproveFetches: window.__bootFetches.filter(item => item.path === '/api/auto-approve').length,
            errors: window.__bootErrors,
            rejections: window.__bootRejections,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in result, result
    assert result["beforeWorking"] is False, result
    assert result["ready"] is True, result
    assert result["connected"] is True, result
    assert result["autoApproveFetches"] >= 2, result
    assert result["errors"] == []
    assert result["rejections"] == []


def test_preferences_scroll_defers_passive_rerender(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof selectSession === 'function' && window.__terminalOpened >= 1")
    )
    opened = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        selectSession('__prefs__').then(
          () => requestAnimationFrame(() => done({ok: true})),
          error => done({ok: false, error: String(error)})
        );
        """
    )
    assert opened["ok"], opened
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.querySelector('.preferences-scroll') !== null")
    )
    metrics = browser.execute_script(
        """
        const scroller = document.querySelector('.preferences-scroll');
        scroller.scrollTop = 60;
        scroller.dispatchEvent(new WheelEvent('wheel', {deltaY: 120, bubbles: true}));
        renderPreferencesPanels();
        const afterPassive = document.querySelector('.preferences-scroll');
        renderPreferencesPanels({force: true});
        const afterForced = document.querySelector('.preferences-scroll');
        return {
          passiveKeptScroller: afterPassive === scroller,
          forcedReplacedScroller: afterForced !== afterPassive,
          scrollTop: afterPassive.scrollTop,
          bodyHtml: document.querySelector('.preferences-body')?.innerHTML || '',
        };
        """
    )
    assert metrics["passiveKeptScroller"], metrics
    assert metrics["forcedReplacedScroller"], metrics
    assert "preferences-sections" in metrics["bodyHtml"]


def test_active_pane_ring_opacity_follows_preference(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof applySettingsPayload === 'function' && document.querySelector('#panel-1') !== null"
        )
    )
    metrics = browser.execute_script(
        """
        const applyOpacity = value => {
          applySettingsPayload({settings: {appearance: {pane_ring_opacity: value}}, defaults: {}, mtime_ns: value}, {force: true});
          const panel = document.querySelector('#panel-1');
          panel.classList.add('active-pane');
          const rootStyle = getComputedStyle(document.documentElement);
          const panelStyle = getComputedStyle(panel);
          const ringOwner = panel.closest('.dv-groupview');
          const ringStyle = ringOwner ? getComputedStyle(ringOwner, '::after') : panelStyle;
          return {
            activeOpacity: rootStyle.getPropertyValue('--pane-active-ring-opacity').trim(),
            normalOpacity: rootStyle.getPropertyValue('--pane-ring-opacity').trim(),
            borderColor: ringStyle.borderLeftColor,
          };
        };
        return {low: applyOpacity(5), defaultish: applyOpacity(75)};
        """
    )
    assert metrics["low"]["activeOpacity"] == "5%", metrics
    assert metrics["low"]["normalOpacity"] == "5%", metrics
    assert metrics["defaultish"]["activeOpacity"] == "75%", metrics
    assert metrics["low"]["borderColor"] != metrics["defaultish"]["borderColor"], metrics


def test_meta_arrow_walks_visible_pane_tabs_in_live_runtime(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1,2,__prefs__&layout=row@50(left,right)&tabs=left:files,1;right:__prefs__,2",
        sessions=["1", "2"],
    )
    wait_for_dockview(browser, min_tabs=4)
    wait_for_dockview_tab_geometry(browser, min_tabs=4, min_width=45)
    result = browser.execute_script(
        """
        const fireMetaArrow = key => {
          const event = new KeyboardEvent('keydown', {
            key,
            code: key,
            metaKey: true,
            ctrlKey: false,
            altKey: false,
            shiftKey: false,
            bubbles: true,
            cancelable: true,
          });
          (document.activeElement || window).dispatchEvent(event);
          return event.defaultPrevented;
        };
        activatePaneTab('left', fileExplorerItemId, {userInitiated: true});
        setFocusedPanelItem(fileExplorerItemId, {userInitiated: true});
        const firstPrevented = fireMetaArrow('ArrowRight');
        const afterFinderRight = {
          left: activeItemForSide('left'),
          right: activeItemForSide('right'),
          focus: visualActivePaneItem(),
        };
        const secondPrevented = fireMetaArrow('ArrowRight');
        const afterPaneSpill = {
          left: activeItemForSide('left'),
          right: activeItemForSide('right'),
          focus: visualActivePaneItem(),
        };
        const thirdPrevented = fireMetaArrow('ArrowLeft');
        const afterBack = {
          left: activeItemForSide('left'),
          right: activeItemForSide('right'),
          focus: visualActivePaneItem(),
        };
        const editor = document.createElement('div');
        editor.className = 'cm-editor';
        editor.tabIndex = 0;
        document.body.appendChild(editor);
        editor.focus();
        const blockedPrevented = fireMetaArrow('ArrowRight');
        return {
          firstPrevented,
          secondPrevented,
          thirdPrevented,
          blockedPrevented,
          afterFinderRight,
          afterPaneSpill,
          afterBack,
          finalLeft: activeItemForSide('left'),
          finalRight: activeItemForSide('right'),
        };
        """
    )
    assert result["firstPrevented"] is True, result
    assert result["afterFinderRight"] == {"left": "1", "right": "__prefs__", "focus": "1"}, result
    assert result["secondPrevented"] is True, result
    assert result["afterPaneSpill"] == {"left": "1", "right": "__prefs__", "focus": "__prefs__"}, result
    assert result["thirdPrevented"] is True, result
    assert result["afterBack"] == {"left": "1", "right": "__prefs__", "focus": "1"}, result
    assert result["blockedPrevented"] is False, result
    assert result["finalLeft"] == "1" and result["finalRight"] == "__prefs__", result


def test_active_color_radios_recolor_live_pane_chrome(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=__files__,1,__prefs__&layout=row@32(slot1,row@56(left,right))&tabs=slot1:__files__;left:1;right:__prefs__")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('input[type="radio"][data-setting-path="appearance.active_color"][value="blue"]') !== null
              && document.querySelector('.dockview-pane-tab[data-pane-tab="1"].active') !== null
              && document.querySelector('#panel-__files__ .file-explorer-mode-toggle[aria-pressed="true"]') !== null
              && document.querySelector('input[data-setting-path="appearance.inactive_pane_opacity"]') !== null
              && document.querySelector('input[data-setting-path="appearance.pane_ring_opacity"]') !== null
            """
        )
    )
    browser.execute_script(
        """
        const panel = document.querySelector('#panel-1');
        panel.classList.add('active-pane', 'focused-pane', 'typing-ready-pane', 'yolo-ready-pane');
        document.getElementById('tabMetaToggle')?.classList.add('active');
        const notify = document.getElementById('notifyToggle');
        notify?.classList.add('notify-toggle', 'active');
        const radio = document.querySelector('input[type="radio"][data-setting-path="appearance.active_color"][value="blue"]');
        radio.checked = true;
        radio.dispatchEvent(new Event('change', {bubbles: true}));
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return window.__settingsPayload?.settings?.appearance?.active_color === 'blue'
              && getComputedStyle(document.querySelector('.dockview-pane-tab[data-pane-tab="1"].active')).backgroundColor === 'rgb(59, 130, 246)';
            """
        )
    )
    metrics = browser.execute_script(
        """
        const rootStyle = getComputedStyle(document.documentElement);
        const bodyStyle = getComputedStyle(document.body);
        const tabStyle = getComputedStyle(document.querySelector('.dockview-pane-tab[data-pane-tab="1"].active'));
        const panelStyle = getComputedStyle(document.querySelector('#panel-1'));
        const prefsRange = document.querySelector('input[data-setting-path="appearance.inactive_pane_opacity"]');
        const ringRange = document.querySelector('input[data-setting-path="appearance.pane_ring_opacity"]');
        const radio = document.querySelector('input[data-setting-path="appearance.date_time_hour_cycle"]');
        const prefsScroll = document.querySelector('.preferences-scroll');
        const finderMode = document.querySelector('#panel-__files__ .file-explorer-mode-toggle[aria-pressed="true"]');
        const tabMeta = document.getElementById('tabMetaToggle');
        const notify = document.getElementById('notifyToggle');
        const brandYo = document.querySelector('.brand-title .brand-yolo');
        const mdProbe = document.createElement('div');
        mdProbe.className = 'markdown-body';
        mdProbe.innerHTML = '<h1>Probe</h1>';
        document.body.appendChild(mdProbe);
        const cmProbe = document.createElement('div');
        cmProbe.className = 'cm-content';
        cmProbe.innerHTML = '<span class="md-heading"># Probe</span>';
        document.body.appendChild(cmProbe);
        const yoloProbe = document.createElement('span');
        yoloProbe.className = 'session-yolo-marker active';
        yoloProbe.textContent = 'YO';
        document.body.appendChild(yoloProbe);
        const shortcutProbe = document.createElement('section');
        shortcutProbe.className = 'keyboard-shortcuts-section';
        shortcutProbe.innerHTML = '<h3>APP</h3>';
        document.body.appendChild(shortcutProbe);
        const activeSwatch = document.querySelector('input[type="radio"][data-setting-path="appearance.active_color"][value="blue"]').closest('.preferences-radio').querySelector('.preferences-radio-swatch');
        const activeSwatchLabel = activeSwatch.closest('.preferences-radio');
        const scrollProbe = document.createElement('div');
        scrollProbe.style.background = 'var(--pane-scrollbar-thumb-active)';
        document.body.appendChild(scrollProbe);
        const expectedScrollThumb = getComputedStyle(scrollProbe).backgroundColor;
        scrollProbe.style.background = 'var(--pane-scrollbar-thumb)';
        const expectedNeutralScrollThumb = getComputedStyle(scrollProbe).backgroundColor;
        scrollProbe.remove();
        const metrics = {
          markdownHeadingColor: getComputedStyle(mdProbe.querySelector('h1')).color,
          cmHeadingColor: getComputedStyle(cmProbe.querySelector('.md-heading')).color,
          yoloBg: getComputedStyle(yoloProbe).backgroundColor,
          yoloBorder: getComputedStyle(yoloProbe).borderTopColor,
          shortcutHeadingColor: getComputedStyle(shortcutProbe.querySelector('h3')).color,
          swatchDisplay: getComputedStyle(activeSwatchLabel).display,
          swatchRadius: getComputedStyle(activeSwatch).borderRadius,
        };
        mdProbe.remove();
        cmProbe.remove();
        yoloProbe.remove();
        shortcutProbe.remove();
        return {
          errors: window.__bootErrors,
          rejections: window.__bootRejections,
          rootAccent: rootStyle.getPropertyValue('--active-accent').trim(),
          bodyAccent: bodyStyle.getPropertyValue('--active-accent').trim(),
          rootRgb: rootStyle.getPropertyValue('--active-accent-rgb').trim(),
          tabBg: tabStyle.backgroundColor,
          tabBorder: tabStyle.borderTopColor,
          panelBorder: panelStyle.borderTopColor,
          prefsRangeAccent: getComputedStyle(prefsRange).accentColor,
          ringRangeAccent: getComputedStyle(ringRange).accentColor,
          radioAccent: getComputedStyle(radio).accentColor,
          prefsScrollColor: getComputedStyle(prefsScroll).scrollbarColor,
          prefsScrollThumb: getComputedStyle(prefsScroll, '::-webkit-scrollbar-thumb').backgroundColor,
          expectedScrollThumb,
          expectedNeutralScrollThumb,
          finderModeBg: getComputedStyle(finderMode).backgroundColor,
          finderModeBorder: getComputedStyle(finderMode).borderTopColor,
          tabMetaBg: getComputedStyle(tabMeta).backgroundColor,
          tabMetaBorder: getComputedStyle(tabMeta).borderTopColor,
          notifyBg: getComputedStyle(notify).backgroundColor,
          brandYoBg: getComputedStyle(brandYo).backgroundColor,
          brandYoBorder: getComputedStyle(brandYo).borderTopColor,
          markdownHeadingColor: metrics.markdownHeadingColor,
          cmHeadingColor: metrics.cmHeadingColor,
          yoloBg: metrics.yoloBg,
          yoloBorder: metrics.yoloBorder,
          shortcutHeadingColor: metrics.shortcutHeadingColor,
          swatchDisplay: metrics.swatchDisplay,
          swatchRadius: metrics.swatchRadius,
          settingsPosts: window.__bootFetches.filter(item => item.method === 'POST' && item.path === '/api/settings').length,
        };
        """
    )
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert metrics["rootAccent"] == "#3b82f6", metrics
    assert metrics["bodyAccent"] == "#3b82f6", metrics
    assert metrics["rootRgb"] == "59 130 246", metrics
    assert metrics["tabBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["tabBorder"] == "rgb(59, 130, 246)", metrics
    assert metrics["panelBorder"].startswith("color(srgb 0.231373 0.509804 0.964706 / 0.75)"), metrics
    assert metrics["prefsRangeAccent"] == "rgb(59, 130, 246)", metrics
    assert metrics["ringRangeAccent"] == "rgb(59, 130, 246)", metrics
    assert metrics["radioAccent"] == "rgb(59, 130, 246)", metrics
    assert metrics["expectedScrollThumb"] == "rgba(255, 234, 0, 0.88)", metrics
    assert metrics["prefsScrollColor"].startswith(metrics["expectedNeutralScrollThumb"]), metrics
    assert metrics["prefsScrollThumb"] == metrics["expectedNeutralScrollThumb"], metrics
    assert metrics["finderModeBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["finderModeBorder"] == "rgb(59, 130, 246)", metrics
    assert metrics["tabMetaBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["tabMetaBorder"] == "rgb(59, 130, 246)", metrics
    assert metrics["notifyBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["brandYoBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["brandYoBorder"] == "rgb(59, 130, 246)", metrics
    assert metrics["markdownHeadingColor"] == "rgb(59, 130, 246)", metrics
    assert metrics["cmHeadingColor"] == "rgb(59, 130, 246)", metrics
    assert metrics["yoloBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["yoloBorder"] == "rgb(59, 130, 246)", metrics
    assert metrics["shortcutHeadingColor"] == "rgb(59, 130, 246)", metrics
    assert metrics["swatchDisplay"] == "grid", metrics
    assert metrics["swatchRadius"] == "2px 0px 0px 2px", metrics
    assert metrics["settingsPosts"] >= 1, metrics
    browser.execute_script(
        """
        const panel = document.querySelector('#panel-__prefs__');
        panel?.classList.add('active-pane', 'focused-pane');
        panel?.style.setProperty('--pane-scrollbar-current-thumb', 'var(--pane-scrollbar-thumb-active)');
        """
    )
    WebDriverWait(browser, 2).until(
        lambda driver: driver.execute_script(
            "return getComputedStyle(document.querySelector('.preferences-scroll'), '::-webkit-scrollbar-thumb').backgroundColor"
        ) == metrics["expectedScrollThumb"]
    )
    browser.execute_script(
        """
        const panel = document.querySelector('#panel-__prefs__');
        panel?.classList.remove('active-pane', 'focused-pane');
        panel?.style.removeProperty('--pane-scrollbar-current-thumb');
        """
    )
    WebDriverWait(browser, 2).until(
        lambda driver: driver.execute_script(
            "return getComputedStyle(document.querySelector('.preferences-scroll'), '::-webkit-scrollbar-thumb').backgroundColor"
        ) == metrics["expectedNeutralScrollThumb"]
    )
    move_to_visible_panel(browser, "panel-1")
    WebDriverWait(browser, 2).until(
        lambda driver: driver.execute_script(
            "return getComputedStyle(document.querySelector('.preferences-scroll'), '::-webkit-scrollbar-thumb').backgroundColor"
        ) == metrics["expectedNeutralScrollThumb"]
    )
    browser.execute_script(
        """
        setFocusedPanelItem('1', {userInitiated: true});
        const radio = document.querySelector('input[type="radio"][data-setting-path="appearance.editor_cursor_color"][value="laser-lime"]');
        radio.checked = true;
        radio.dispatchEvent(new Event('change', {bubbles: true}));
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return window.__settingsPayload?.settings?.appearance?.editor_cursor_color === 'laser-lime'
              && getComputedStyle(document.documentElement).getPropertyValue('--active-terminal-cursor-rgb').trim() === '204 255 0'
              && terminals.get('1')?.term?.options?.theme?.cursor === '#ccff00';
            """
        )
    )
    cursor_metrics = browser.execute_script(
        """
        const probe = document.createElement('div');
        probe.style.background = 'var(--pane-scrollbar-thumb-active)';
        document.body.appendChild(probe);
        const activeThumb = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return {
          rootCursorRgb: getComputedStyle(document.documentElement).getPropertyValue('--active-terminal-cursor-rgb').trim(),
          terminalCursor: terminals.get('1')?.term?.options?.theme?.cursor || '',
          activeScrollbarThumb: activeThumb,
        };
        """
    )
    assert cursor_metrics["rootCursorRgb"] == "204 255 0", cursor_metrics
    assert cursor_metrics["terminalCursor"] == "#ccff00", cursor_metrics
    assert cursor_metrics["activeScrollbarThumb"] == "rgba(204, 255, 0, 0.88)", cursor_metrics
    browser.execute_script(
        """
        const radio = document.querySelector('input[type="radio"][data-setting-path="appearance.active_color"][value="yellow"]');
        radio.checked = true;
        radio.dispatchEvent(new Event('change', {bubbles: true}));
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const brandYo = document.querySelector('.brand-title .brand-yolo');
            return window.__settingsPayload?.settings?.appearance?.active_color === 'yellow'
              && getComputedStyle(brandYo).backgroundColor === 'rgb(234, 179, 8)'
              && getComputedStyle(brandYo).borderTopColor === 'rgb(234, 179, 8)';
            """
        )
    )


def test_info_and_preferences_scrollbars_inherit_shared_hover_state(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=__info__,__prefs__,1&layout=row@34(left,row@50(mid,right))&tabs=left:__info__;mid:__prefs__;right:1",
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('.info-list') !== null && document.querySelector('.preferences-scroll') !== null"
        )
    )
    metrics = browser.execute_script(
        """
        const info = document.querySelector('.info-list');
        const prefs = document.querySelector('.preferences-scroll');
        info.insertAdjacentHTML('beforeend', '<div style="height: 900px"></div>');
        prefs.insertAdjacentHTML('beforeend', '<div style="height: 900px"></div>');
        const probe = document.createElement('div');
        probe.style.background = 'var(--pane-scrollbar-thumb)';
        document.body.appendChild(probe);
        const neutral = getComputedStyle(probe).backgroundColor;
        probe.style.background = 'var(--pane-scrollbar-thumb-active)';
        const accent = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return {
          neutral,
          accent,
          infoOverflow: info.scrollHeight > info.clientHeight,
          prefsOverflow: prefs.scrollHeight > prefs.clientHeight,
          infoThumb: getComputedStyle(info, '::-webkit-scrollbar-thumb').backgroundColor,
          prefsThumb: getComputedStyle(prefs, '::-webkit-scrollbar-thumb').backgroundColor,
        };
        """
    )
    assert metrics["infoOverflow"], metrics
    assert metrics["prefsOverflow"], metrics
    assert metrics["infoThumb"] == metrics["neutral"], metrics
    assert metrics["prefsThumb"] == metrics["neutral"], metrics

    def thumb(selector):
        return browser.execute_script(
            "return getComputedStyle(document.querySelector(arguments[0]), '::-webkit-scrollbar-thumb').backgroundColor",
            selector,
        )

    def wait_thumb(selector, expected):
        WebDriverWait(browser, 2).until(lambda _driver: thumb(selector) == expected)

    browser.execute_script("document.querySelector('.info-list')?.closest('.panel')?.classList.add('active-pane', 'focused-pane')")
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".info-list")).perform()
    wait_thumb(".info-list", metrics["accent"])
    wait_thumb(".preferences-scroll", metrics["neutral"])

    browser.execute_script(
        """
        document.querySelector('.info-list')?.closest('.panel')?.classList.remove('active-pane', 'focused-pane');
        document.querySelector('.preferences-scroll')?.closest('.panel')?.classList.add('active-pane', 'focused-pane');
        """
    )
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".preferences-scroll")).perform()
    wait_thumb(".preferences-scroll", metrics["accent"])
    wait_thumb(".info-list", metrics["neutral"])

    browser.execute_script("document.querySelector('.preferences-scroll')?.closest('.panel')?.classList.remove('active-pane', 'focused-pane')")
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".preferences-scroll")).perform()
    wait_thumb(".preferences-scroll", metrics["neutral"])

    ActionChains(browser).move_to_element(browser.find_element("css selector", ".topbar")).perform()
    wait_thumb(".info-list", metrics["neutral"])
    wait_thumb(".preferences-scroll", metrics["neutral"])


@pytest.mark.parametrize("width, expected_rows", [(860, [3, 3]), (493, [1, 2, 2, 1])])
def test_pane_tabs_stay_within_panel(browser, tmp_path, width, expected_rows):
    # Tabs wrap to fit the panel at any width: the toolbar never overflows the panel, the rows wrap to the
    # expected counts, every tab stays within the panel's right edge, and the toolbar stays centered with no
    # gap below the tab head. (Was two near-identical width tests, at 860 and 493.)
    metrics = load_fixture(browser, tmp_path, width)
    assert metrics["toolbar"]["right"] <= metrics["panel"]["right"]
    assert [row["count"] for row in metrics["rows"]] == expected_rows
    assert all(tab["right"] <= metrics["panel"]["right"] for tab in metrics["tabs"])
    assert metrics["toolbarCenterDelta"] <= 2
    assert metrics["tabHeadBottomGap"] <= 2


def test_pane_tab_wide_layout_shows_compact_detail_row(browser, tmp_path):
    # At a comfortable width the first tab row shares the toolbar's row (sits left of it), lower rows stay
    # within the panel, and the detail row is a single compact strip (text shown, symbol hidden, tinted bg).
    metrics = load_fixture(browser, tmp_path, 860)
    first_row = metrics["rows"][0]
    assert max(first_row["rights"]) < metrics["toolbar"]["left"]
    lower_row_rights = [right for row in metrics["rows"][1:] for right in row["rights"]]
    assert max(lower_row_rights) <= metrics["panel"]["right"]
    assert metrics["detailRow"]["height"] <= 20
    assert metrics["hiddenTextDisplay"] != "none"
    assert metrics["hiddenSymbolDisplay"] == "none"
    assert metrics["detailBg"] != "rgb(18, 24, 35)"
    assert metrics["detailCloseRightGap"] <= 3


def test_pane_tab_active_accent_theming(browser, tmp_path):
    # The active pane tab + the pressed control tab share one --active-accent source (asserted as
    # relationships, not pinned greens, so the appearance.active_color picker can't break it); unpressed
    # controls share one neutral bg; theme-specific surfaces repaint on a theme switch while everything else
    # stays token-equal; and inactive-tab dir text always contrasts with its bg (no white-on-white).
    load_fixture(browser, tmp_path, 860)
    theme_metrics = browser.execute_script(
        """
        const originalPanel = document.querySelector('.panel.active-pane');
        const inactivePanel = originalPanel.cloneNode(true);
        inactivePanel.classList.remove('active-pane');
        inactivePanel.style.marginTop = '12px';
        document.body.appendChild(inactivePanel);
        const readMetrics = () => {
          const panel = document.querySelector('.panel.active-pane');
          const activeTab = panel.querySelector('.pane-tab.active');
          const inactiveActiveTab = inactivePanel.querySelector('.pane-tab.active');
          const inactiveTab = panel.querySelector('.pane-tab:not(.active)');
          const panelHead = panel.querySelector('.panel-head');
          const toolbarActive = panel.querySelector('.panel-head .tab.active:not(.auto-toggle)');
          const activeWindow = panel.querySelector('.tmux-window-button.active[data-window-agent]');
          const inactiveWindow = panel.querySelector('.tmux-window-button[data-window-agent]:not(.active)');
          const paneControl = panel.querySelector('.tabs .pane-minimize');
          const zoomControl = panel.querySelector('.tabs .pc-zoom');
          return {
            panelBorder: getComputedStyle(panel).borderTopColor,
            panelHeadBg: getComputedStyle(panelHead).backgroundColor,
            activeTabBg: getComputedStyle(activeTab).backgroundColor,
            activeTabColor: getComputedStyle(activeTab).color,
            activeTabShadow: getComputedStyle(activeTab).boxShadow,
            inactiveActiveTabBg: getComputedStyle(inactiveActiveTab).backgroundColor,
            inactiveActiveTabColor: getComputedStyle(inactiveActiveTab).color,
            inactiveActiveTabShadow: getComputedStyle(inactiveActiveTab).boxShadow,
            inactiveTabBg: getComputedStyle(inactiveTab).backgroundColor,
            inactiveTabBorder: getComputedStyle(inactiveTab).borderTopColor,
            inactiveDirColor: getComputedStyle(inactiveTab.querySelector('.session-button-dir') || inactiveTab).color,
            toolbarActiveBg: getComputedStyle(toolbarActive).backgroundColor,
            toolbarActiveBorder: getComputedStyle(toolbarActive).borderTopColor,
            activeWindowBg: getComputedStyle(activeWindow).backgroundColor,
            activeWindowBorder: getComputedStyle(activeWindow).borderTopColor,
            activeWindowColor: getComputedStyle(activeWindow).color,
            inactiveWindowBg: getComputedStyle(inactiveWindow).backgroundColor,
            paneControlBg: getComputedStyle(paneControl).backgroundColor,
            paneControlBorder: getComputedStyle(paneControl).borderTopColor,
            zoomControlBg: getComputedStyle(zoomControl).backgroundColor,
          };
        };
        const dark = readMetrics();
        document.body.classList.add('theme-light');
        return {dark, light: readMetrics()};
        """
    )
    assert theme_metrics["dark"]["panelHeadBg"].startswith("color(srgb")
    # The light chrome strip is a tinted (active-accent-derived) bar, NOT the neutral control bg — assert
    # the relationship, not a pinned green, so it survives the appearance.active_color picker.
    assert theme_metrics["light"]["panelHeadBg"] != theme_metrics["light"]["paneControlBg"]
    # Shared pane-chrome buttons (image 009): every UNPRESSED control is white (light) / near-black (dark)
    # via --pane-ctl-bg — including the expand "+" (formerly always-green). Only PRESSED/ACTIVE buttons go
    # green (asserted via toolbarActiveBg below). No per-button one-off colors.
    assert theme_metrics["dark"]["paneControlBg"] == "rgb(27, 36, 50)"
    assert theme_metrics["light"]["paneControlBg"] == "rgb(247, 249, 252)"
    assert theme_metrics["dark"]["zoomControlBg"] == "rgb(27, 36, 50)"      # "+" is NOT green when unpressed
    assert theme_metrics["light"]["zoomControlBg"] == "rgb(247, 249, 252)"
    assert theme_metrics["dark"]["zoomControlBg"] == theme_metrics["dark"]["paneControlBg"]  # all unpressed controls share one bg
    # The active control tab (the agent/"claude" pill) is PRESSED -> green, in both themes (shared rule).
    # The pressed/active control tab is the active accent (NOT a pinned green) — distinct from the
    # unpressed control bg in both themes, so the picker (Green/Blue/...) doesn't break the test.
    assert theme_metrics["dark"]["toolbarActiveBg"] != theme_metrics["dark"]["paneControlBg"]
    assert theme_metrics["light"]["toolbarActiveBg"] != theme_metrics["light"]["paneControlBg"]
    # the active-tab greens are tuned PER THEME so a theme switch visibly repaints the active
    # pane tab; the frame controls are also theme-specific now (image 043). Every OTHER surface stays
    # token-equal across themes.
    # inactiveTabBg is theme-specific now (images 003/004): light gets a very-light-green #e6f1dd while
    # dark keeps #285a2f, so it must NOT be required equal across themes.
    # toolbarActiveBg/Border are the PRESSED control tab's green, which is theme-specific (light #4f9e3a /
    # dark #86d600); detail-row bg now follows --pane-bar-bg so it is theme-specific too.
    theme_specific = {"panelHeadBg", "activeTabBg", "activeTabColor", "inactiveActiveTabBg", "inactiveActiveTabColor", "inactiveTabBg", "inactiveTabBorder", "inactiveDirColor", "paneControlBg", "paneControlBorder", "zoomControlBg", "toolbarActiveBg", "toolbarActiveBorder", "activeWindowBg", "activeWindowBorder", "activeWindowColor", "inactiveWindowBg"}
    for key, value in theme_metrics["dark"].items():
        if key not in theme_specific:
            assert theme_metrics["light"][key] == value
    # The active pane tab shares the active accent with the pressed control tab (one --active-accent
    # source) and stands out from the unpressed control bg — true for any active-color preset.
    assert theme_metrics["dark"]["activeTabBg"] == theme_metrics["dark"]["toolbarActiveBg"]
    assert theme_metrics["light"]["activeTabBg"] == theme_metrics["light"]["toolbarActiveBg"]
    assert theme_metrics["dark"]["activeWindowBg"] == theme_metrics["dark"]["activeTabBg"]
    assert theme_metrics["light"]["activeWindowBg"] == theme_metrics["light"]["activeTabBg"]
    assert theme_metrics["light"]["activeWindowBg"] != theme_metrics["light"]["inactiveWindowBg"]
    assert theme_metrics["light"]["activeWindowColor"] != theme_metrics["light"]["activeWindowBg"]
    assert theme_metrics["dark"]["activeTabBg"] != theme_metrics["dark"]["paneControlBg"]
    assert theme_metrics["light"]["activeTabBg"] != theme_metrics["dark"]["activeTabBg"]
    assert theme_metrics["light"]["inactiveActiveTabBg"] != theme_metrics["dark"]["inactiveActiveTabBg"]
    # Active-tab text stays legible against its (theme-specific) accent in BOTH modes.
    assert theme_metrics["light"]["activeTabColor"] != theme_metrics["light"]["activeTabBg"]
    assert theme_metrics["dark"]["activeTabColor"] != theme_metrics["dark"]["activeTabBg"]
    assert theme_metrics["dark"]["activeTabShadow"] == "none"
    # images 003/004: an unfocused pane's active tab now uses the SAME full green as the focused pane's
    # active tab (no lightening) — the unfocused-active tokens are aliased to the focused ones.
    assert theme_metrics["dark"]["inactiveActiveTabBg"] == theme_metrics["dark"]["activeTabBg"]
    assert theme_metrics["dark"]["inactiveActiveTabShadow"] == "none"
    # REGRESSION GUARD (image 008): the inactive-tab branch/dir TEXT must contrast with the tab bg in BOTH
    # themes — i.e. NOT white-on-white. This is the check that was missing before: the prior browser test
    # measured tab BACKGROUNDS but never the nested .session-button-* TEXT color, so a near-white dir text
    # on a near-white light tab went uncaught. Compare relative luminance of text vs bg.
    def _lum(css_rgb):
        nums = [int(n) for n in re.findall(r"\d+", css_rgb)[:3]]
        return 0.2126 * nums[0] + 0.7152 * nums[1] + 0.0722 * nums[2]
    for th in ("light", "dark"):
        text_lum = _lum(theme_metrics[th]["inactiveDirColor"])
        bg_lum = _lum(theme_metrics[th]["inactiveTabBg"])
        assert abs(text_lum - bg_lum) > 80, (
            f"{th}: inactive-tab dir text ({theme_metrics[th]['inactiveDirColor']}) must contrast with the "
            f"tab bg ({theme_metrics[th]['inactiveTabBg']}) — not white-on-white"
        )


def test_split_pane_seam_is_a_compact_tile_divider(browser, tmp_path):
    load_split_seam_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const topPanel = document.getElementById('top-panel');
        const bottomPanel = document.getElementById('bottom-panel');
        const resizer = document.getElementById('split-resizer');
        const topRect = topPanel.getBoundingClientRect();
        const bottomRect = bottomPanel.getBoundingClientRect();
        const resizerRect = resizer.getBoundingClientRect();
        const topStyle = getComputedStyle(topPanel);
        const bottomStyle = getComputedStyle(bottomPanel);
        return {
          seamGap: bottomRect.top - topRect.bottom,
          resizerHeight: resizerRect.height,
          topBottomBorder: topStyle.borderBottomWidth,
          bottomTopBorder: bottomStyle.borderTopWidth,
          topBottomRadius: topStyle.borderBottomLeftRadius,
          bottomTopRadius: bottomStyle.borderTopLeftRadius,
        };
        """
    )
    assert metrics["resizerHeight"] <= 2
    assert metrics["seamGap"] <= 2.5
    assert metrics["topBottomBorder"] == "0px"
    assert metrics["bottomTopBorder"] == "0px"
    assert metrics["topBottomRadius"] == "0px"
    assert metrics["bottomTopRadius"] == "0px"


def test_tab_menu_rows_are_compact_for_many_tabs(browser, tmp_path):
    metrics = load_menu_fixture(browser, tmp_path)
    assert metrics["count"] == 30
    assert metrics["maxHeight"] <= 23
    assert metrics["maxStep"] <= 24
    assert metrics["firstTwentyFiveSpan"] <= 575
    assert metrics["width"] > 0
    assert metrics["width"] <= metrics["maxInlineSize"] + metrics["devicePixelRatio"]
    assert metrics["secondRowBorderTopColor"] != "rgba(0, 0, 0, 0)"
    assert metrics["scrollHeight"] <= 700


def test_topbar_uses_ui_font_size_and_compact_actions(browser, tmp_path):
    load_topbar_font_fixture(browser, tmp_path)
    topbar_metrics = browser.execute_script(
        """
        const menu = document.getElementById('menu-file');
        const action = document.getElementById('tabMetaToggle');
        const paneTab = document.querySelector('.pane-tab');
        const menuRect = menu.getBoundingClientRect();
        const actionRect = action.getBoundingClientRect();
        const paneTabRect = paneTab.getBoundingClientRect();
        return {
          menuFontSize: Number.parseFloat(getComputedStyle(menu).fontSize),
          menuHeight: menuRect.height,
          actionWidth: actionRect.width,
          actionHeight: actionRect.height,
          paneTabHeight: paneTabRect.height,
        };
        """
    )
    assert topbar_metrics["menuFontSize"] >= 17.5
    assert 23 <= topbar_metrics["menuHeight"] <= 25
    assert 22 <= topbar_metrics["paneTabHeight"] <= 24
    assert topbar_metrics["actionWidth"] <= 31
    assert topbar_metrics["actionHeight"] <= 31
    compact_metrics = browser.execute_script(
        """
        document.documentElement.style.setProperty('--ui-font-size', '13px');
        document.documentElement.style.setProperty('--tab-label-size', '13px');
        const action = document.getElementById('tabMetaToggle').getBoundingClientRect();
        const paneTab = document.querySelector('.pane-tab').getBoundingClientRect();
        return {actionWidth: action.width, actionHeight: action.height, paneTabHeight: paneTab.height};
        """
    )
    assert compact_metrics["actionWidth"] <= 21
    assert compact_metrics["actionHeight"] <= 21
    assert compact_metrics["paneTabHeight"] <= 21
    tiny_metrics = browser.execute_script(
        """
        document.documentElement.style.setProperty('--ui-font-size', '8px');
        document.documentElement.style.setProperty('--tab-label-size', '8px');
        const action = document.getElementById('tabMetaToggle').getBoundingClientRect();
        const paneTab = document.querySelector('.pane-tab').getBoundingClientRect();
        return {actionHeight: action.height, paneTabHeight: paneTab.height};
        """
    )
    assert tiny_metrics["actionHeight"] <= 18
    assert tiny_metrics["paneTabHeight"] <= 18


def test_active_pane_tab_container_lightens_in_dark_only(browser, tmp_path):
    load_fixture(browser, tmp_path, 860)
    metrics = browser.execute_script(
        """
        function colorFor(styleValue) {
          const probe = document.createElement('div');
          probe.style.position = 'absolute';
          probe.style.left = '-1000px';
          probe.style.top = '-1000px';
          probe.style.background = styleValue;
          document.body.appendChild(probe);
          const color = getComputedStyle(probe).backgroundColor;
          probe.remove();
          return color;
        }
        function brightness(color) {
          const nums = (color.match(/\\d+(?:\\.\\d+)?/g) || []).slice(0, 3).map(Number);
          if (color.startsWith('color(srgb')) return nums.reduce((sum, value) => sum + value * 255, 0);
          return nums[0] + nums[1] + nums[2];
        }
        document.body.classList.add('theme-dark');
        const head = document.querySelector('.panel-head');
        const darkStrip = colorFor('var(--pane-tab-strip-bg)');
        const darkHead = getComputedStyle(head).backgroundColor;
        document.body.classList.remove('theme-dark');
        document.body.classList.add('theme-light');
        const lightStrip = colorFor('var(--pane-tab-strip-bg)');
        const lightHead = getComputedStyle(head).backgroundColor;
        return {
          darkStrip,
          darkHead,
          darkStripBrightness: brightness(darkStrip),
          lightStrip,
          lightHead,
        };
        """
    )
    assert metrics["darkHead"] == metrics["darkStrip"], metrics
    assert metrics["darkStripBrightness"] > 0, metrics
    assert metrics["lightHead"] == metrics["lightStrip"], metrics


def test_pane_tab_strip_hover_token_is_removed():
    # The dark-only --pane-tab-strip-hover-bg was removed when the tab container + info bar were unified
    # onto one token. Cheap string guard against its reintroduction — no browser needed (P3 demotion).
    css = app_css()
    assert "--pane-tab-strip-hover-bg" not in css


def test_share_host_editor_snapshot_tracks_codemirror_cursor_after_typing(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof fileEditorItemFor === 'function'
              && typeof applyLayoutSlots === 'function'
              && typeof shareUiStateSnapshot === 'function'
              && document.querySelector('#grid') !== null;
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          autoFocusEnabled = false;
          activeShares = [{
            token: 'share-token',
            shortId: 'share-token',
            mode: 'ro',
            scheme: 'http',
            session: '1',
            sessions: ['1'],
            viewers: 1,
            maxViewers: 5,
            expiresAt: Math.floor(Date.now() / 1000) + 600,
          }];
          const path = '/home/test/yolomux.dev/docs/DONE.md';
          const item = fileEditorItemFor(path);
          const content = [
            '# DONE',
            '',
            'First paragraph stays visible.',
            'Second paragraph receives the typed text.',
            'Third paragraph is only here to keep normal editor structure.',
          ].join('\\n');
          setFileState(path, {
            kind: 'text',
            content,
            original: content,
            dirty: false,
            language: 'markdown',
            gitRoot: '/home/test/yolomux.dev',
            gitTracked: true,
            gitHasHistory: true,
            gitHistory: [{ref: 'HEAD'}],
          });
          setFileEditorViewMode(path, 'edit', item);
          registerFileEditorLayoutItem(path, {item});
          const next = emptyLayoutSlots();
          next[layoutTreeKey] = leafNode('left');
          next.left = paneStateWithTabs([item], item);
          applyLayoutSlots(next, {focusSession: item, forceFull: true});
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 220; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => panelNodes.get(item)?._cmView?.scrollDOM);
          if (!ready) return {error: 'CodeMirror editor did not initialize', bootErrors: window.__bootErrors || [], bootRejections: window.__bootRejections || []};
          const panel = panelNodes.get(item);
          const view = panel._cmView;
          fileEditorViewState.set(item, {scrollTop: 0, scrollLeft: 0, anchor: 0, head: 0, scrollSnapshot: null});
          const insertAt = content.indexOf('receives');
          const insert = 'typed ';
          view.focus();
          view.dispatch({
            changes: {from: insertAt, to: insertAt, insert},
            selection: {anchor: insertAt + insert.length, head: insertAt + insert.length},
          });
          await frame();
          await frame();
          const cached = fileEditorViewState.get(item) || {};
          const snapshot = shareUiStateSnapshot();
          const modeEntry = (snapshot.editor?.modes || []).find(entry => entry.item === item || entry.path === path) || {};
          return {
            item,
            expectedAnchor: insertAt + insert.length,
            cachedAnchor: cached.anchor,
            cachedHead: cached.head,
            snapshotAnchor: modeEntry.viewState?.anchor,
            snapshotHead: modeEntry.viewState?.head,
            dirty: openFiles.get(path)?.dirty === true,
            sentSockets: window.__bootSockets || [],
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["dirty"] is True, metrics
    assert metrics["cachedAnchor"] == metrics["expectedAnchor"], metrics
    assert metrics["cachedHead"] == metrics["expectedAnchor"], metrics
    assert metrics["snapshotAnchor"] == metrics["expectedAnchor"], metrics
    assert metrics["snapshotHead"] == metrics["expectedAnchor"], metrics


def test_long_markdown_editor_scroll_survives_preferences_tab_roundtrip(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof fileEditorItemFor === 'function'
              && typeof applyLayoutSlots === 'function'
              && typeof createFileEditorPanel === 'function'
              && document.querySelector('#grid') !== null;
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          autoFocusEnabled = false;
          const path = '/home/test/repo/2026.md';
          const item = fileEditorItemFor(path);
          const content = Array.from({length: 1400}, (_value, index) => `# Entry ${index + 1}\\n\\n- Work item ${index + 1} with enough text to produce normal Markdown editor rows.`).join('\\n');
          setFileState(path, {
            kind: 'text',
            content,
            original: content,
            dirty: false,
            language: 'markdown',
            gitRoot: '/home/test/repo',
            gitTracked: true,
            gitHasHistory: true,
            gitHistory: [{ref: 'HEAD'}],
          });
          setFileEditorViewMode(path, 'edit', item);
          registerFileEditorLayoutItem(path);
          const next = emptyLayoutSlots();
          next[layoutTreeKey] = leafNode('left');
          next.left = paneStateWithTabs([item, prefsItemId], item);
          applyLayoutSlots(next, {focusSession: item, forceFull: true});
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 220; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => {
            const panel = panelNodes.get(item);
            const scroller = panel?._cmView?.scrollDOM;
            return activeItemForSide('left') === item
              && panel?.isConnected
              && scroller
              && scroller.scrollHeight > scroller.clientHeight * 3;
          });
          if (!ready) {
            const panel = panelNodes.get(item);
            const scroller = panel?._cmView?.scrollDOM;
            const rect = panel?.getBoundingClientRect?.();
            return {
              error: 'file editor did not become scrollable',
              active: activeItemForSide('left'),
              item,
              panelExists: Boolean(panel),
              connected: Boolean(panel?.isConnected),
              panelHeight: rect?.height || 0,
              hasView: Boolean(panel?._cmView),
              scrollHeight: scroller?.scrollHeight || 0,
              clientHeight: scroller?.clientHeight || 0,
              cmText: panel?.querySelector?.('.file-editor-codemirror-panel')?.textContent?.slice(0, 80) || '',
              bootErrors: window.__bootErrors || [],
              bootRejections: window.__bootRejections || [],
            };
          }
          const panel = panelNodes.get(item);
          const scroller = panel._cmView.scrollDOM;
          scroller.scrollTop = Math.min(9000, scroller.scrollHeight - scroller.clientHeight - 10);
          await frame();
          await frame();
          const savedTop = scroller.scrollTop;
          activatePaneTab('left', prefsItemId, {userInitiated: true});
          const prefsReady = await waitFor(() => activeItemForSide('left') === prefsItemId && panelNodes.get(prefsItemId)?.isConnected);
          const captured = fileEditorViewState.get(item);
          const capturedTop = captured?.scrollTop || 0;
          const capturedSnapshot = Boolean(captured?.scrollSnapshot);
          if (!prefsReady) return {error: 'preferences tab did not activate', savedTop, capturedTop, capturedSnapshot};
          activatePaneTab('left', item, {userInitiated: true});
          const fileReady = await waitFor(() => activeItemForSide('left') === item && panelNodes.get(item)?.isConnected && panelNodes.get(item)?._cmView?.scrollDOM);
          if (!fileReady) return {error: 'file tab did not reactivate', savedTop, capturedTop, capturedSnapshot};
          await frame();
          await frame();
          await new Promise(resolve => setTimeout(resolve, 140));
          await frame();
          const restoredPanel = panelNodes.get(item);
          const restoredScroller = restoredPanel._cmView.scrollDOM;
          return {
            savedTop,
            capturedTop,
            capturedSnapshot,
            restoredTop: restoredScroller.scrollTop,
            scrollHeight: restoredScroller.scrollHeight,
            clientHeight: restoredScroller.clientHeight,
            active: activeItemForSide('left'),
            focusedPanelItem,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["capturedSnapshot"] is True, metrics
    assert abs(metrics["capturedTop"] - metrics["savedTop"]) < 32, metrics
    assert abs(metrics["restoredTop"] - metrics["savedTop"]) < 32, metrics


def test_long_markdown_editor_scroll_survives_dockview_tab_click_roundtrip(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof applyLayoutSlots === 'function' && typeof registerFileEditorLayoutItem === 'function';"
        )
    )
    setup = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          autoFocusEnabled = false;
          const path = '/home/test/repo/2026.md';
          const item = fileEditorItemFor(path);
          const content = Array.from({length: 1400}, (_value, index) => `# Entry ${index + 1}\\n\\n- Work item ${index + 1} with enough text to produce normal Markdown editor rows.`).join('\\n');
          setFileState(path, {
            kind: 'text',
            content,
            original: content,
            dirty: false,
            language: 'markdown',
            gitRoot: '/home/test/repo',
            gitTracked: true,
            gitHasHistory: true,
            gitHistory: [{ref: 'HEAD'}],
          });
          setFileEditorViewMode(path, 'edit', item);
          registerFileEditorLayoutItem(path);
          const next = emptyLayoutSlots();
          next[layoutTreeKey] = leafNode('left');
          next.left = paneStateWithTabs([item, prefsItemId], item);
          applyLayoutSlots(next, {focusSession: item, forceFull: true});
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 260; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => {
            const panel = panelNodes.get(item);
            const scroller = panel?._cmView?.scrollDOM;
            return dockviewLayoutActive()
              && activeItemForSide('left') === item
              && panel?.isConnected
              && scroller
              && scroller.scrollHeight > scroller.clientHeight * 3
              && Array.from(document.querySelectorAll('.dockview-pane-tab')).some(tab => tab.dataset.paneTab === item)
              && Array.from(document.querySelectorAll('.dockview-pane-tab')).some(tab => tab.dataset.paneTab === prefsItemId);
          });
          if (!ready) {
            const panel = panelNodes.get(item);
            const scroller = panel?._cmView?.scrollDOM;
            return {
              error: 'dockview editor did not become ready',
              dockview: typeof dockviewLayoutActive === 'function' ? dockviewLayoutActive() : null,
              active: activeItemForSide('left'),
              panelExists: Boolean(panel),
              connected: Boolean(panel?.isConnected),
              hasView: Boolean(panel?._cmView),
              scrollHeight: scroller?.scrollHeight || 0,
              clientHeight: scroller?.clientHeight || 0,
              tabs: Array.from(document.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
            };
          }
          const panel = panelNodes.get(item);
          const scroller = panel._cmView.scrollDOM;
          scroller.scrollTop = Math.min(9000, scroller.scrollHeight - scroller.clientHeight - 10);
          await frame();
          await frame();
          return {
            item,
            savedTop: scroller.scrollTop,
            preSwitchCapturedTop: fileEditorViewState.get(item)?.scrollTop || 0,
            clientHeight: scroller.clientHeight,
            scrollHeight: scroller.scrollHeight,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in setup, setup
    assert setup["savedTop"] > setup["clientHeight"], setup

    def dockview_tab(item):
        return WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                return Array.from(document.querySelectorAll('.dockview-pane-tab'))
                  .find(tab => tab.dataset.paneTab === arguments[0]) || null;
                """,
                item,
            )
        )

    ActionChains(browser).move_to_element(dockview_tab("__prefs__")).click().perform()
    after_prefs = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const item = arguments[0];
            if (activeItemForSide('left') !== prefsItemId) return null;
            const state = fileEditorViewState.get(item);
            return {
              active: activeItemForSide('left'),
              capturedTop: state?.scrollTop || 0,
              capturedSnapshot: Boolean(state?.scrollSnapshot),
              panelConnected: Boolean(panelNodes.get(item)?.isConnected),
            };
            """,
            setup["item"],
        )
    )
    assert after_prefs["capturedSnapshot"] is True, after_prefs
    assert abs(after_prefs["capturedTop"] - setup["savedTop"]) < 32, {**setup, **after_prefs}

    ActionChains(browser).move_to_element(dockview_tab(setup["item"])).click().perform()
    restored = browser.execute_async_script(
        """
        const item = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 220; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => activeItemForSide('left') === item && panelNodes.get(item)?.isConnected && panelNodes.get(item)?._cmView?.scrollDOM);
          await frame();
          await frame();
          await new Promise(resolve => setTimeout(resolve, 140));
          await frame();
          const panel = panelNodes.get(item);
          const scroller = panel?._cmView?.scrollDOM;
          return {
            ready,
            active: activeItemForSide('left'),
            restoredTop: scroller?.scrollTop || 0,
            scrollHeight: scroller?.scrollHeight || 0,
            clientHeight: scroller?.clientHeight || 0,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """,
        setup["item"],
    )
    assert restored["ready"] is True, restored
    assert abs(restored["restoredTop"] - setup["savedTop"]) < 32, {**setup, **after_prefs, **restored}


def test_preferences_scroll_survives_dockview_tab_click_roundtrip(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof applyLayoutSlots === 'function' && typeof paneViewState !== 'undefined';"
        )
    )
    setup = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          autoFocusEnabled = false;
          const next = emptyLayoutSlots();
          next[layoutTreeKey] = leafNode('left');
          next.left = paneStateWithTabs([prefsItemId, infoItemId], prefsItemId);
          applyLayoutSlots(next, {focusSession: prefsItemId, forceFull: true});
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 240; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => {
            const scroller = panelNodes.get(prefsItemId)?.querySelector('.preferences-scroll');
            return dockviewLayoutActive()
              && activeItemForSide('left') === prefsItemId
              && scroller
              && scroller.scrollHeight > scroller.clientHeight * 2
              && Array.from(document.querySelectorAll('.dockview-pane-tab')).some(tab => tab.dataset.paneTab === prefsItemId)
              && Array.from(document.querySelectorAll('.dockview-pane-tab')).some(tab => tab.dataset.paneTab === infoItemId);
          });
          if (!ready) {
            const scroller = panelNodes.get(prefsItemId)?.querySelector('.preferences-scroll');
            return {
              error: 'preferences pane did not become scrollable',
              active: activeItemForSide('left'),
              scrollHeight: scroller?.scrollHeight || 0,
              clientHeight: scroller?.clientHeight || 0,
              tabs: Array.from(document.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
            };
          }
          const scroller = panelNodes.get(prefsItemId).querySelector('.preferences-scroll');
          scroller.scrollTop = Math.min(9000, scroller.scrollHeight - scroller.clientHeight - 10);
          await frame();
          await frame();
          return {
            item: prefsItemId,
            other: infoItemId,
            savedTop: scroller.scrollTop,
            preSwitchCapturedTop: paneViewState.get(prefsItemId)?.scrollContainers?.find(entry => entry.scrollTop > 0)?.scrollTop || 0,
            clientHeight: scroller.clientHeight,
            scrollHeight: scroller.scrollHeight,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in setup, setup
    assert setup["savedTop"] > setup["clientHeight"], setup
    assert abs(setup["preSwitchCapturedTop"] - setup["savedTop"]) < 32, setup

    def dockview_tab(item):
        return WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                return Array.from(document.querySelectorAll('.dockview-pane-tab'))
                  .find(tab => tab.dataset.paneTab === arguments[0]) || null;
                """,
                item,
            )
        )

    ActionChains(browser).move_to_element(dockview_tab(setup["other"])).click().perform()
    after_other = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            if (activeItemForSide('left') !== arguments[0]) return null;
            const state = paneViewState.get(arguments[1]);
            return {
              active: activeItemForSide('left'),
              capturedTop: state?.scrollContainers?.find(entry => entry.scrollTop > 0)?.scrollTop || 0,
            };
            """,
            setup["other"],
            setup["item"],
        )
    )
    assert abs(after_other["capturedTop"] - setup["savedTop"]) < 32, {**setup, **after_other}

    ActionChains(browser).move_to_element(dockview_tab(setup["item"])).click().perform()
    restored = browser.execute_async_script(
        """
        const item = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          for (let attempt = 0; attempt < 80; attempt += 1) {
            if (activeItemForSide('left') === item && panelNodes.get(item)?.querySelector('.preferences-scroll')) break;
            await frame();
          }
          await frame();
          await frame();
          await new Promise(resolve => setTimeout(resolve, 120));
          const scroller = panelNodes.get(item)?.querySelector('.preferences-scroll');
          return {
            active: activeItemForSide('left'),
            restoredTop: scroller?.scrollTop || 0,
            clientHeight: scroller?.clientHeight || 0,
            scrollHeight: scroller?.scrollHeight || 0,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """,
        setup["item"],
    )
    assert restored["active"] == setup["item"], restored
    assert abs(restored["restoredTop"] - setup["savedTop"]) < 32, {**setup, **after_other, **restored}


def test_info_scroll_survives_dockview_tab_click_roundtrip(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof applyLayoutSlots === 'function' && typeof renderInfoPanel === 'function';"
        )
    )
    setup = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          autoFocusEnabled = false;
          infoPanelSubTab = 'info';
          transcriptMetaLoaded = true;
          transcriptMetaLoading = false;
          transcriptMetaLoadError = '';
          const branches = Array.from({length: 180}, (_value, index) => ({
            name: `feature/long-info-row-${index + 1}`,
            subject: `Long YO!info row ${index + 1} that makes the branch table scroll.`,
            updated: `2026-06-${String((index % 28) + 1).padStart(2, '0')}`,
            updated_ts: 1800000000 - index,
            current: index === 0,
            linear_ids: [`YOLO-${index + 1}`],
          }));
          transcriptMeta = {
            session_order: ['1'],
            sessions: {
              '1': {
                session: '1',
                project: {
                  git: {
                    root: '/home/test/repo',
                    cwd: '/home/test/repo',
                    branch: 'feature/long-info-row-1',
                    other_branches: {branches},
                  },
                  linear: [],
                },
              },
            },
          };
          const next = emptyLayoutSlots();
          next[layoutTreeKey] = leafNode('left');
          next.left = paneStateWithTabs([infoItemId, prefsItemId], infoItemId);
          applyLayoutSlots(next, {focusSession: infoItemId, forceFull: true});
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 260; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => {
            const scroller = document.getElementById('info-content');
            return dockviewLayoutActive()
              && activeItemForSide('left') === infoItemId
              && scroller
              && scroller.scrollHeight > scroller.clientHeight * 2
              && Array.from(document.querySelectorAll('.dockview-pane-tab')).some(tab => tab.dataset.paneTab === infoItemId)
              && Array.from(document.querySelectorAll('.dockview-pane-tab')).some(tab => tab.dataset.paneTab === prefsItemId);
          });
          if (!ready) {
            const scroller = document.getElementById('info-content');
            return {
              error: 'info pane did not become scrollable',
              active: activeItemForSide('left'),
              scrollHeight: scroller?.scrollHeight || 0,
              clientHeight: scroller?.clientHeight || 0,
              rows: document.querySelectorAll('#info-content .info-row').length,
              tabs: Array.from(document.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
            };
          }
          const scroller = document.getElementById('info-content');
          scroller.scrollTop = Math.min(9000, scroller.scrollHeight - scroller.clientHeight - 10);
          await frame();
          await frame();
          return {
            item: infoItemId,
            other: prefsItemId,
            savedTop: scroller.scrollTop,
            preSwitchCapturedTop: paneViewState.get(infoItemId)?.scrollContainers?.find(entry => entry.scrollTop > 0)?.scrollTop || 0,
            clientHeight: scroller.clientHeight,
            scrollHeight: scroller.scrollHeight,
            rowCount: document.querySelectorAll('#info-content .info-row').length,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in setup, setup
    assert setup["rowCount"] > 100, setup
    assert setup["savedTop"] > setup["clientHeight"], setup
    assert abs(setup["preSwitchCapturedTop"] - setup["savedTop"]) < 32, setup

    def dockview_tab(item):
        return WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                return Array.from(document.querySelectorAll('.dockview-pane-tab'))
                  .find(tab => tab.dataset.paneTab === arguments[0]) || null;
                """,
                item,
            )
        )

    ActionChains(browser).move_to_element(dockview_tab(setup["other"])).click().perform()
    after_other = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            if (activeItemForSide('left') !== arguments[0]) return null;
            const state = paneViewState.get(arguments[1]);
            return {
              active: activeItemForSide('left'),
              capturedTop: state?.scrollContainers?.find(entry => entry.scrollTop > 0)?.scrollTop || 0,
            };
            """,
            setup["other"],
            setup["item"],
        )
    )
    assert abs(after_other["capturedTop"] - setup["savedTop"]) < 32, {**setup, **after_other}

    ActionChains(browser).move_to_element(dockview_tab(setup["item"])).click().perform()
    restored = browser.execute_async_script(
        """
        const item = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          for (let attempt = 0; attempt < 100; attempt += 1) {
            if (activeItemForSide('left') === item && document.getElementById('info-content')) break;
            await frame();
          }
          await frame();
          await frame();
          await new Promise(resolve => setTimeout(resolve, 120));
          const scroller = document.getElementById('info-content');
          return {
            active: activeItemForSide('left'),
            restoredTop: scroller?.scrollTop || 0,
            clientHeight: scroller?.clientHeight || 0,
            scrollHeight: scroller?.scrollHeight || 0,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """,
        setup["item"],
    )
    assert restored["active"] == setup["item"], restored
    assert abs(restored["restoredTop"] - setup["savedTop"]) < 32, {**setup, **after_other, **restored}


def test_topbar_finder_and_modified_files_headers_hover_accent_in_light_mode(browser, tmp_path):
    def theme_tokens():
        return browser.execute_script(
            """
            document.body.classList.add('theme-light');
            function tokenColor(name) {
              const probe = document.createElement('div');
              probe.style.background = `var(${name})`;
              probe.style.position = 'absolute';
              probe.style.left = '-1000px';
              probe.style.top = '-1000px';
              document.body.appendChild(probe);
              const color = getComputedStyle(probe).backgroundColor;
              probe.remove();
              return color;
            }
            return {
              panel: tokenColor('--panel'),
              neutral: tokenColor('--panel2'),
              accent: tokenColor('--pane-tab-strip-bg'),
            };
            """
        )

    def background(selector):
        return browser.execute_script("return getComputedStyle(document.querySelector(arguments[0])).backgroundColor", selector)

    def wait_background(selector, expected):
        WebDriverWait(browser, 2).until(lambda _driver: background(selector) == expected)

    load_topbar_font_fixture(browser, tmp_path)
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".pane-tab")).perform()
    tokens = theme_tokens()
    wait_background("#topbar-fixture", tokens["neutral"])
    ActionChains(browser).move_to_element(browser.find_element("id", "topbar-fixture")).perform()
    wait_background("#topbar-fixture", tokens["accent"])
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".pane-tab")).perform()
    wait_background("#topbar-fixture", tokens["neutral"])

    load_finder_click_toolbar_fixture(browser, tmp_path)
    tokens = theme_tokens()
    wait_background("#finder-panel .file-explorer-head", tokens["neutral"])
    ActionChains(browser).move_to_element(browser.find_element("css selector", "#finder-panel .file-explorer-head")).perform()
    wait_background("#finder-panel .file-explorer-head", tokens["accent"])
    ActionChains(browser).move_to_element(browser.find_element("id", "terminal-panel")).perform()
    wait_background("#finder-panel .file-explorer-head", tokens["neutral"])

    activate_finder_diff_fixture(browser)
    wait_background("#modified-files-panel .changes-toolbar", tokens["panel"])
    ActionChains(browser).move_to_element(browser.find_element("id", "modified-files-panel")).perform()
    wait_background("#finder-panel .file-explorer-head", tokens["neutral"])
    wait_background("#modified-files-panel .changes-toolbar", tokens["accent"])


def test_finder_and_embedded_differ_scrollbars_hover_independently(browser, tmp_path):
    load_finder_click_toolbar_fixture(browser, tmp_path)
    browser.execute_script(
        """
        const tree = document.querySelector('.file-explorer-tree-panel');
        tree.innerHTML = '<div style="height: 520px"></div>';
        """
    )

    def thumb(selector):
        return browser.execute_script(
            "return getComputedStyle(document.querySelector(arguments[0]), '::-webkit-scrollbar-thumb').backgroundColor",
            selector,
        )

    def wait_thumb(selector, expected):
        WebDriverWait(browser, 2).until(lambda _driver: thumb(selector) == expected)

    neutral = "rgba(190, 205, 218, 0.56)"
    accent = browser.execute_script(
        """
        const probe = document.createElement('div');
        probe.style.background = 'var(--pane-scrollbar-thumb-active)';
        document.body.appendChild(probe);
        const color = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return color;
        """
    )
    overflow = browser.execute_script(
        """
        const tree = document.querySelector('.file-explorer-tree-panel');
        return {
          tree: tree.scrollHeight > tree.clientHeight,
        };
        """
    )
    assert overflow["tree"]

    wait_thumb(".file-explorer-tree-panel", neutral)
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".file-explorer-tree-panel")).perform()
    wait_thumb(".file-explorer-tree-panel", accent)
    browser.execute_script("document.getElementById('finder-panel')?.classList.remove('active-pane', 'focused-pane')")
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".file-explorer-tree-panel")).perform()
    wait_thumb(".file-explorer-tree-panel", neutral)
    ActionChains(browser).move_to_element(browser.find_element("id", "terminal-panel")).perform()
    wait_thumb(".file-explorer-tree-panel", neutral)

    activate_finder_diff_fixture(browser)
    browser.execute_script(
        """
        const differ = document.getElementById('modified-files-panel');
        differ.insertAdjacentHTML('beforeend', '<div style="height: 520px"></div>');
        """
    )
    overflow = browser.execute_script(
        """
        const differ = document.getElementById('modified-files-panel');
        return {differ: differ.scrollHeight > differ.clientHeight};
        """
    )
    assert overflow["differ"]
    wait_thumb("#modified-files-panel", neutral)
    browser.execute_script("document.getElementById('finder-panel')?.classList.add('active-pane', 'focused-pane')")
    ActionChains(browser).move_to_element(browser.find_element("id", "modified-files-panel")).perform()
    wait_thumb("#modified-files-panel", accent)
    browser.execute_script("document.getElementById('finder-panel')?.classList.remove('active-pane', 'focused-pane')")
    ActionChains(browser).move_to_element(browser.find_element("id", "modified-files-panel")).perform()
    wait_thumb("#modified-files-panel", neutral)
    ActionChains(browser).move_to_element(browser.find_element("id", "terminal-panel")).perform()
    wait_thumb("#modified-files-panel", neutral)


def test_finder_differ_row_hover_and_embedded_refresh_are_visible_in_light_mode(browser, tmp_path):
    load_finder_click_toolbar_fixture(browser, tmp_path)
    activate_finder_diff_fixture(browser)
    refresh_metrics = browser.execute_script(
        """
        document.body.classList.add('theme-light');
        const button = document.querySelector('#modified-files-panel .changes-refresh');
        const style = getComputedStyle(button);
        const before = getComputedStyle(button, '::before');
        const rect = button.getBoundingClientRect();
        return {
          background: style.backgroundColor,
          borderColor: style.borderTopColor,
          color: style.color,
          beforeContent: before.content,
          beforeDisplay: before.display,
          beforeFontSize: Number.parseFloat(before.fontSize),
          height: rect.height,
          width: rect.width,
        };
        """
    )
    assert refresh_metrics["background"] != "rgb(255, 255, 255)"
    assert refresh_metrics["color"] != "rgb(255, 255, 255)"
    assert refresh_metrics["borderColor"] != "rgb(255, 255, 255)"
    assert refresh_metrics["beforeContent"] == '"↻"'
    assert refresh_metrics["beforeDisplay"] != "none"
    assert refresh_metrics["beforeFontSize"] >= 12
    assert refresh_metrics["height"] >= 18
    assert refresh_metrics["width"] >= 20

    load_pc_controls_fixture(browser, tmp_path)
    hover_tokens = browser.execute_script(
        """
        document.body.classList.add('theme-light');
        const probe = document.createElement('div');
        probe.style.position = 'absolute';
        probe.style.left = '-1000px';
        probe.style.top = '-1000px';
        probe.style.background = 'var(--file-hover-bg)';
        document.body.appendChild(probe);
        const hoverBg = getComputedStyle(probe).backgroundColor;
        probe.style.background = 'var(--file-hover-border)';
        const hoverBorder = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return {hoverBg, hoverBorder};
        """
    )
    ActionChains(browser).move_to_element(browser.find_element("id", "collapsed-dir")).perform()
    row_metrics = browser.execute_script(
        """
        const row = document.getElementById('collapsed-dir');
        const style = getComputedStyle(row);
        return {
          background: style.backgroundColor,
          boxShadow: style.boxShadow,
        };
        """
    )
    assert hover_tokens["hoverBg"] == "rgb(255, 242, 168)"
    assert row_metrics["background"] == hover_tokens["hoverBg"]
    assert hover_tokens["hoverBorder"] in row_metrics["boxShadow"]


def test_finder_sync_current_file_reuses_selected_row_colors(browser, tmp_path):
    load_pc_controls_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const read = () => {
          const selected = getComputedStyle(document.getElementById('selected-file-row'));
          const current = getComputedStyle(document.getElementById('current-file-row'));
          const selectedName = getComputedStyle(document.querySelector('#selected-file-row .file-tree-name'));
          const currentName = getComputedStyle(document.querySelector('#current-file-row .file-tree-name'));
          return {
            selectedColor: selected.color,
            currentColor: current.color,
            selectedNameColor: selectedName.color,
            currentNameColor: currentName.color,
            selectedBg: selected.backgroundColor,
            currentBg: current.backgroundColor,
            selectedShadow: selected.boxShadow,
            currentShadow: current.boxShadow,
          };
        };
        document.body.classList.remove('theme-light');
        document.body.classList.add('theme-dark');
        const dark = read();
        document.body.classList.remove('theme-dark');
        document.body.classList.add('theme-light');
        const light = read();
        return {dark, light};
        """
    )
    for theme in ("dark", "light"):
        assert metrics[theme]["currentColor"] == metrics[theme]["selectedColor"], metrics
        assert metrics[theme]["currentNameColor"] == metrics[theme]["selectedNameColor"], metrics
        assert metrics[theme]["currentBg"] == metrics[theme]["selectedBg"], metrics
        assert metrics[theme]["currentShadow"] == metrics[theme]["selectedShadow"], metrics


def test_finder_differ_status_badges_share_one_column(browser, tmp_path):
    load_file_tree_status_alignment_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const rowIds = ['status-row-m', 'status-row-t', 'status-row-q'];
        const rows = rowIds.map(id => {
          const row = document.getElementById(id);
          const status = row.querySelector('.file-tree-git-status');
          const date = row.querySelector('.file-tree-date');
          const rowRect = row.getBoundingClientRect();
          const statusRect = status.getBoundingClientRect();
          const dateRect = date.getBoundingClientRect();
          return {
            statusCenterX: statusRect.left + statusRect.width / 2,
            statusCenterY: statusRect.top + statusRect.height / 2,
            rowCenterY: rowRect.top + rowRect.height / 2,
            statusRight: statusRect.right,
            dateLeft: dateRect.left,
            dateRight: dateRect.right,
          };
        });
        const xs = rows.map(row => row.statusCenterX);
        const centerYs = rows.map(row => Math.abs(row.statusCenterY - row.rowCenterY));
        const dateRights = rows.map(row => row.dateRight);
        return {
          statusCenterDelta: Math.max(...xs) - Math.min(...xs),
          maxVerticalDelta: Math.max(...centerYs),
          dateRightDelta: Math.max(...dateRights) - Math.min(...dateRights),
          statusBeforeDate: rows.every(row => row.statusRight <= row.dateLeft + 0.5),
        };
        """
    )
    assert metrics["statusCenterDelta"] <= 0.75
    assert metrics["dateRightDelta"] <= 0.75
    assert metrics["maxVerticalDelta"] <= 1.0
    assert metrics["statusBeforeDate"]
    hidden_date_metrics = browser.execute_script(
        """
        const row = document.getElementById('status-row-m');
        const status = row.querySelector('.file-tree-git-status');
        const diff = row.querySelector('.file-tree-diff');
        const date = row.querySelector('.file-tree-date');
        const beforeStatusRight = status.getBoundingClientRect().right;
        const beforeDiffRight = diff.getBoundingClientRect().right;
        date.hidden = true;
        const rowRect = row.getBoundingClientRect();
        const statusRect = status.getBoundingClientRect();
        const diffRect = diff.getBoundingClientRect();
        return {
          dateDisplay: getComputedStyle(date).display,
          statusGain: statusRect.right - beforeStatusRight,
          diffGain: diffRect.right - beforeDiffRight,
          statusRightGap: rowRect.right - statusRect.right,
        };
        """
    )
    assert hidden_date_metrics["dateDisplay"] == "none"
    assert hidden_date_metrics["statusGain"] >= 80
    assert hidden_date_metrics["diffGain"] >= 80
    assert hidden_date_metrics["statusRightGap"] <= 10


def test_differ_long_filename_ellipsizes_before_date_column(browser, tmp_path):
    load_file_tree_status_alignment_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const row = document.getElementById('status-row-long');
        const shortRow = document.getElementById('status-row-m');
        const tree = row.parentElement;
        const name = row.querySelector('.file-tree-name');
        const agent = row.querySelector('.file-tree-agent');
        const diff = row.querySelector('.file-tree-diff');
        const status = row.querySelector('.file-tree-git-status');
        const date = row.querySelector('.file-tree-date');
        const shortName = shortRow.querySelector('.file-tree-name');
        const shortAgent = shortRow.querySelector('.file-tree-agent');
        const rowRect = row.getBoundingClientRect();
        const treeRect = tree.getBoundingClientRect();
        const nameRect = name.getBoundingClientRect();
        const agentRect = agent.getBoundingClientRect();
        const diffRect = diff.getBoundingClientRect();
        const statusRect = status.getBoundingClientRect();
        const dateRect = date.getBoundingClientRect();
        const shortNameRect = shortName.getBoundingClientRect();
        const shortAgentRect = shortAgent.getBoundingClientRect();
        return {
          treeRight: treeRect.right,
          rowRight: rowRect.right,
          nameRight: nameRect.right,
          agentLeft: agentRect.left,
          diffLeft: diffRect.left,
          statusLeft: statusRect.left,
          dateLeft: dateRect.left,
          dateRight: dateRect.right,
          dateClientWidth: date.clientWidth,
          dateScrollWidth: date.scrollWidth,
          nameClientWidth: name.clientWidth,
          nameScrollWidth: name.scrollWidth,
          nameFlex: getComputedStyle(name).flex,
          agentMarginInlineEnd: getComputedStyle(agent).marginInlineEnd,
          shortNameRight: shortNameRect.right,
          shortAgentLeft: shortAgentRect.left,
          shortNameFlex: getComputedStyle(shortName).flex,
        };
        """
    )
    assert metrics["dateRight"] <= metrics["treeRight"] + 0.5, metrics
    assert metrics["dateScrollWidth"] <= metrics["dateClientWidth"] + 1, metrics
    assert metrics["nameScrollWidth"] > metrics["nameClientWidth"] + 1, metrics
    assert metrics["nameFlex"].startswith("0 1"), metrics
    assert metrics["shortNameFlex"].startswith("0 1"), metrics
    assert metrics["agentMarginInlineEnd"] == "0px", metrics
    assert metrics["nameRight"] <= metrics["agentLeft"] + 0.5, metrics
    assert metrics["shortAgentLeft"] - metrics["shortNameRight"] <= 8, metrics
    assert metrics["agentLeft"] <= metrics["diffLeft"] <= metrics["statusLeft"] <= metrics["dateLeft"], metrics


def test_diff_overview_does_not_cover_editor_scrollbar(browser, tmp_path):
    load_codemirror_scrollbar_overview_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const hostRect = document.getElementById('host').getBoundingClientRect();
        const overviewRect = document.getElementById('overview').getBoundingClientRect();
        const overviewStyle = getComputedStyle(document.getElementById('overview'));
        const scroller = document.getElementById('scroller');
        const scrollerRect = scroller.getBoundingClientRect();
        const scrollbarStyle = getComputedStyle(scroller, '::-webkit-scrollbar');
        const cornerStyle = getComputedStyle(scroller, '::-webkit-scrollbar-corner');
        document.getElementById('overview').style.top = '0px';
        document.getElementById('overview').style.bottom = 'auto';
        document.getElementById('overview').style.height = `${scroller.clientHeight}px`;
        const adjustedOverviewRect = document.getElementById('overview').getBoundingClientRect();
        const verticalTrackBottom = scrollerRect.top + scroller.clientHeight;
        return {
          overviewRightGap: hostRect.right - adjustedOverviewRect.right,
          overviewTopDelta: Math.abs(adjustedOverviewRect.top - scrollerRect.top),
          overviewBottomDelta: Math.abs(adjustedOverviewRect.bottom - verticalTrackBottom),
          overviewWidth: adjustedOverviewRect.width,
          overviewBackground: overviewStyle.backgroundImage,
          overviewPointerEvents: overviewStyle.pointerEvents,
          tickCount: document.querySelectorAll('.cm-diff-overview-tick').length,
          scrollbarWidth: Number.parseFloat(scrollbarStyle.width || '0'),
          cornerBackground: cornerStyle.backgroundColor,
        };
        """
    )
    assert metrics["overviewRightGap"] >= 12
    assert metrics["overviewTopDelta"] <= 1
    assert metrics["overviewBottomDelta"] <= 1
    assert 3 <= metrics["overviewWidth"] <= 5
    assert "linear-gradient" in metrics["overviewBackground"]
    assert metrics["overviewPointerEvents"] == "none"
    assert metrics["tickCount"] == 0
    assert 11 <= metrics["scrollbarWidth"] <= 13
    assert metrics["cornerBackground"] in {
        "rgba(255, 255, 255, 0.04)",
        "rgba(255, 255, 255, 0.05)",
        "rgba(15, 23, 42, 0.1)",
    }


def test_diff_overview_matches_actual_todo_codemirror_rows(browser, tmp_path):
    load_codemirror_todo_diff_overview_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return window.__todoDiffOverviewMetrics != null")
    )
    metrics = browser.execute_script("return window.__todoDiffOverviewMetrics")
    original_text, current_text = codemirror_todo_diff_overview_texts()
    original_lines = original_text.splitlines(keepends=True)
    current_lines = current_text.splitlines(keepends=True)
    common_prefix_lines = 0
    while (
        common_prefix_lines < min(len(original_lines), len(current_lines))
        and original_lines[common_prefix_lines] == current_lines[common_prefix_lines]
    ):
        common_prefix_lines += 1
    common_suffix_lines = 0
    while (
        common_suffix_lines < len(original_lines) - common_prefix_lines
        and common_suffix_lines < len(current_lines) - common_prefix_lines
        and original_lines[len(original_lines) - common_suffix_lines - 1] == current_lines[len(current_lines) - common_suffix_lines - 1]
    ):
        common_suffix_lines += 1
    expected_from = sum(len(line) for line in original_lines[:common_prefix_lines])
    expected_to_a = len(original_text) - sum(len(line) for line in original_lines[len(original_lines) - common_suffix_lines:])
    expected_to_b = len(current_text) - sum(len(line) for line in current_lines[len(current_lines) - common_suffix_lines:])
    # Both fixture sides are frozen (codemirror_todo_diff_overview_texts) as one contiguous block
    # replacement, so the merge view is a single chunk with stable byte offsets derived from the
    # actual common prefix/suffix instead of the moving docs/TODO.md.
    assert len(metrics["chunks"]) == 1
    chunk = metrics["chunks"][0]
    assert chunk["fromA"] == expected_from
    assert chunk["toA"] in {expected_to_a, expected_to_a + 1}
    assert chunk["endA"] == chunk["toA"] - 1
    assert chunk["fromB"] == expected_from
    assert chunk["toB"] in {expected_to_b, expected_to_b + 1}
    assert chunk["endB"] == chunk["toB"] - 1
    bands = metrics["rows"]["bands"]
    assert len(bands) == len(metrics["chunks"]) * 2, bands
    for index in range(0, len(bands), 2):
        assert bands[index]["kind"] == "remove", bands
        assert bands[index + 1]["kind"] == "add", bands
        assert bands[index + 1]["start"] == bands[index]["end"], bands
    deleted_rows = metrics["rows"]["deletedRows"]
    current_line_count = metrics["rows"]["currentLineCount"]
    inserted_rows = metrics["insertedRangeRows"]
    todo_line_count = current_text.count("\n") + 1
    assert current_line_count == todo_line_count, (current_line_count, todo_line_count)
    assert metrics["rows"]["totalRows"] == deleted_rows + current_line_count
    assert deleted_rows > 0 and inserted_rows > 0
    assert metrics["removedRangeRows"] == deleted_rows
    assert 0 < metrics["deletedDomRows"] <= metrics["removedRangeRows"]
    assert "linear-gradient" in metrics["overviewBackground"]
    assert metrics["overviewStops"] == metrics["expectedStops"], metrics["overviewBackground"]
    assert metrics["tickCount"] == 0
    assert metrics["overviewTopDelta"] <= 1
    assert metrics["overviewBottomDelta"] <= 1


def test_diff_wrapped_inserted_line_continuation_rows_show_text(browser, tmp_path):
    # Regression for the screenshot bug: a long INSERTED line that soft-wraps in the unified merge diff
    # rendered only its first visual row; continuation rows were blank green (gutter numbers present,
    # text buried). Root cause was the full-bleed box-shadow/clip-path being applied to the INLINE
    # cm-insertedLine/cm-deletedLine marks, letting the parent .cm-changedLine block band paint over the
    # wrapped rows. Assert each wrapped continuation row has VISIBLE inserted text (bounding-box height
    # > 0 AND non-empty caret text AND the inserted mark is the topmost painted element), with word-wrap
    # on and the collapsed-unchanged state active.
    load_codemirror_diff_wrapped_inserted_line_fixture(browser, tmp_path)
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        window.__diffWrapMetrics().then(done);
        """
    )
    assert metrics["insertedFound"] is True, metrics
    assert metrics["hasTailText"] is True, metrics
    # The inserted bullet is long enough to occupy more than one visual row in a 460px pane.
    assert metrics["wraps"] is True, metrics
    assert metrics["boundingHeight"] > metrics["lineHeight"] * 1.5, metrics
    assert metrics["rowCount"] >= 2, metrics
    # Every continuation visual row must paint visible inserted text (height > 0 and non-empty content),
    # not a blank band. This is the exact assertion the box demands.
    continuation_rows = metrics["rows"][1:]
    assert continuation_rows, metrics
    for row in continuation_rows:
        assert row["topElInsideInserted"] is True, (row, metrics)
        assert row["caretTextLen"] > 0, (row, metrics)
    assert metrics["continuationRowsAllVisible"] is True, metrics
    # W5: the collapsed-unchanged widget stays in normal flow and does not overlap the inserted block;
    # it only LOOKED like it floated over the green block because the continuation rows were blank.
    assert metrics["collapsePresent"] is True, metrics
    assert metrics["collapsePosition"] == "static", metrics
    assert metrics["collapseOverlapPx"] == 0, metrics


def test_diff_overview_matches_actual_file_explorer_visible_rows_after_scroll(browser, tmp_path):
    load_codemirror_file_explorer_diff_overview_fixture(browser, tmp_path)
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        window.__fileExplorerDiffOverviewMetrics().then(done);
        """
    )
    assert metrics["chunks"] == [
        {
            "fromA": 56602,
            "toA": 138459,
            "endA": 138458,
            "fromB": 56602,
            "toB": 144134,
            "endB": 144133,
        }
    ]
    assert metrics["tickCount"] == 0
    assert metrics["initialBackground"] != metrics["finalBackground"]
    assert metrics["initialBackground"] == ""
    assert metrics["initialOverviewPresent"] is False
    assert metrics["initialDeletedDomRows"] == 0
    assert metrics["initialChangedStops"] == []
    assert metrics["fullRows"]["deletedRows"] == 1986
    assert metrics["finalOverviewPresent"] is True
    assert metrics["finalChangedStops"] == metrics["expectedFullChangedStops"], metrics["finalBackground"]
    assert any(stop["color"] == '#ff5d6c' for stop in metrics["finalChangedStops"])
    assert any(stop["color"] == '#38d878' for stop in metrics["finalChangedStops"])
    cases = {case["name"]: case for case in metrics["cases"]}
    assert cases["top-normal"]["deletedDomRows"] == 0
    assert cases["red-middle-previous-regression"]["deletedDomRows"] == 1986
    checked_cases = [
        cases["red-middle-previous-regression"],
        cases["red-late-previous-regression"],
        cases["green-middle"],
    ]
    for case in checked_cases:
        assert case["mismatches"] == [], f"{case['name']} mismatched visible rows: {case['mismatches']}"
    for case in cases.values():
        if not case["railPresent"]:
            assert case["background"] == ""
            assert not any(sample["rail"] == "remove" for sample in case["samples"]), case
    assert any(sample["visible"] == "normal" for sample in cases["top-normal"]["samples"])
    assert any(sample["visible"] == "remove" for sample in cases["red-middle-previous-regression"]["samples"])
    assert any(sample["visible"] == "remove" for sample in cases["red-late-previous-regression"]["samples"])
    assert any(sample["visible"] == "add" for sample in cases["green-middle"]["samples"])


def test_diff_left_gutter_stays_neutral(browser, tmp_path):
    load_codemirror_scrollbar_overview_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const changed = document.getElementById('changed-gutter');
        const deleted = document.getElementById('deleted-gutter');
        const mergeRevert = document.getElementById('merge-revert');
        const changedStyle = getComputedStyle(changed);
        const deletedStyle = getComputedStyle(deleted);
        const mergeRevertStyle = getComputedStyle(mergeRevert);
        return {
          changedBg: changedStyle.backgroundColor,
          deletedBg: deletedStyle.backgroundColor,
          changedColor: changedStyle.color,
          deletedColor: deletedStyle.color,
          mergeRevertDisplay: mergeRevertStyle.display,
        };
        """
    )
    assert metrics["changedBg"] == "rgba(0, 0, 0, 0)"
    assert metrics["deletedBg"] == "rgba(0, 0, 0, 0)"
    assert metrics["changedColor"] == metrics["deletedColor"]
    assert metrics["mergeRevertDisplay"] == "none"


def test_finder_path_is_first_and_readable_in_wrapped_toolbar(browser, tmp_path):
    load_finder_click_toolbar_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const toolbar = document.querySelector('#finder-panel .file-explorer-toolbar');
        const primaryRow = toolbar.querySelector('.file-explorer-primary-row');
        const pathRow = toolbar.querySelector('.file-explorer-path-row');
        const actionsRow = toolbar.querySelector('.file-explorer-actions-row');
        const scopeRow = toolbar.querySelector('.file-explorer-scope-row');
        const collapse = primaryRow.querySelector('[data-session-files-collapse-toggle]');
        const newFile = actionsRow.querySelector('[data-file-explorer-new-file]');
        const newFolder = actionsRow.querySelector('[data-file-explorer-new-folder]');
        const actionsSpacer = actionsRow.querySelector('.file-explorer-toolbar-spacer');
        const sync = pathRow.querySelector('.file-explorer-root-mode-toggle-panel');
        const hidden = actionsRow.querySelector('.file-explorer-hidden-toggle-panel');
        const sort = actionsRow.querySelector('.file-explorer-sort-select');
        const quick = toolbar.querySelector('.file-explorer-quick-access-panel');
        const path = pathRow.querySelector('.file-explorer-path-inline');
        const copy = pathRow.querySelector('.file-explorer-path-copy-panel');
        const mode = primaryRow.querySelector('.file-explorer-mode-switcher');
        const diffSession = primaryRow.querySelector('.file-explorer-diff-session-control');
        const primarySpacer = primaryRow.querySelector('.file-explorer-toolbar-spacer');
        const modeButtons = Array.from(mode.querySelectorAll('[data-file-explorer-mode-set]'));
        const modeLabels = Array.from(mode.querySelectorAll('.file-explorer-mode-label'));
        const cluster = toolbar.querySelector('.file-explorer-date-reload-cluster');
        const date = cluster.querySelector('.file-explorer-date-toggle');
        const refresh = cluster.querySelector('.changes-refresh');
        const close = primaryRow.querySelector('.file-explorer-panel-close');
        const toolbarRect = toolbar.getBoundingClientRect();
        const primaryRowRect = primaryRow.getBoundingClientRect();
        const pathRowRect = pathRow.getBoundingClientRect();
        const actionsRowRect = actionsRow.getBoundingClientRect();
        const newFileRect = newFile.getBoundingClientRect();
        const newFolderRect = newFolder.getBoundingClientRect();
        const syncRect = sync.getBoundingClientRect();
        const hiddenRect = hidden.getBoundingClientRect();
        const sortRect = sort.getBoundingClientRect();
        const pathRect = path.getBoundingClientRect();
        const copyRect = copy.getBoundingClientRect();
        const modeRect = mode.getBoundingClientRect();
        const diffSessionRect = diffSession.getBoundingClientRect();
        const modeButtonRects = modeButtons.map(button => button.getBoundingClientRect());
        const modeButtonStyles = modeButtons.map(button => getComputedStyle(button));
        const clusterRect = cluster.getBoundingClientRect();
        const dateRect = date.getBoundingClientRect();
        const refreshRect = refresh.getBoundingClientRect();
        const closeRect = close.getBoundingClientRect();
        const textProbe = document.createElement('span');
        textProbe.style.color = 'var(--text)';
        document.body.appendChild(textProbe);
        const textColor = getComputedStyle(textProbe).color;
        textProbe.remove();
        const colorFor = value => {
          const probe = document.createElement('span');
          probe.style.color = value;
          document.body.appendChild(probe);
          const color = getComputedStyle(probe).color;
          probe.remove();
          return color;
        };
        const tabFont = getComputedStyle(document.documentElement).getPropertyValue('--tab-font').trim();
        return {
          firstRowIsPrimary: toolbar.firstElementChild === primaryRow,
          secondRowIsPath: primaryRow.nextElementSibling === pathRow,
          thirdRowIsActions: pathRow.nextElementSibling === actionsRow,
          noScopeRow: scopeRow === null,
          noQuickAccessPanel: quick === null,
          modeFirstInPrimaryRow: primaryRow.firstElementChild === mode,
          noPanelTitle: primaryRow.querySelector('.file-explorer-panel-title') === null,
          actionsOrder: actionsRow.firstElementChild === newFile && newFile.nextElementSibling === newFolder,
          folderIconPresent: newFolder.querySelector('.file-explorer-folder-icon') !== null,
          pathRowOrder: pathRow.firstElementChild === sync && sync.nextElementSibling === path && path.nextElementSibling === copy,
          hiddenBeforeSort: newFolder.nextElementSibling === actionsSpacer && actionsSpacer.nextElementSibling === hidden && hidden.nextElementSibling === sort,
          syncText: sync.textContent.trim(),
          syncPressed: sync.getAttribute('aria-pressed'),
          rootPressedCount: [sync].filter(button => button.getAttribute('aria-pressed') === 'true').length,
          diffSessionImmediatelyAfterMode: mode.nextElementSibling === diffSession,
          spacerAfterDiffSession: diffSession.nextElementSibling === primarySpacer,
          diffSessionVisibleInFilesMode: getComputedStyle(diffSession).display !== 'none',
          noTopCollapseButton: collapse === null,
          newFileLeft: newFileRect.left,
          newFileRight: newFileRect.right,
          newFolderLeft: newFolderRect.left,
          syncLeft: syncRect.left,
          syncRight: syncRect.right,
          hiddenLeft: hiddenRect.left,
          hiddenRight: hiddenRect.right,
          sortLeft: sortRect.left,
          pathRowTop: pathRowRect.top,
          pathRowBottom: pathRowRect.bottom,
          pathRowLeft: pathRowRect.left,
          pathRowRight: pathRowRect.right,
          pathRowWidth: pathRowRect.width,
          pathLeft: pathRect.left,
          pathRight: pathRect.right,
          primaryRowLeft: primaryRowRect.left,
          primaryRowRight: primaryRowRect.right,
          primaryRowWidth: primaryRowRect.width,
          diffSessionLeft: diffSessionRect.left,
          diffSessionRight: diffSessionRect.right,
          copyLeft: copyRect.left,
          copyRight: copyRect.right,
          copyWidth: copyRect.width,
          modeLeft: modeRect.left,
          modeRight: modeRect.right,
          modeWidth: modeRect.width,
          modeMaxButtonWidth: Math.max(...modeButtonRects.map(rect => rect.width)),
          modeButtonPaddingInline: Array.from(new Set(modeButtonStyles.map(style => `${style.paddingLeft}/${style.paddingRight}`))).sort(),
          modeButtonHorizontal: modeButtonRects.every(rect => rect.width > rect.height),
          modeLabelsHorizontal: modeLabels.every(label => getComputedStyle(label).writingMode === 'horizontal-tb'),
          modeUsesTabFont: modeButtonStyles.every(style => style.fontFamily === tabFont || style.fontFamily.toLowerCase().includes('narrow')),
          modeButtonTopRounded: modeButtonStyles.every(style => style.borderTopLeftRadius !== '0px' && style.borderTopRightRadius !== '0px' && style.borderBottomLeftRadius === '0px'),
          activeModeUsesPaneTabColor: getComputedStyle(mode.querySelector('[aria-pressed="true"]')).backgroundColor === colorFor('var(--pane-tab-active-bg)'),
          modeTexts: Array.from(mode.querySelectorAll('[data-file-explorer-mode-set]')).map(button => button.textContent.trim()),
          pathConsumesRemaining: pathRect.width >= pathRowRect.width - syncRect.width - copyRect.width - 36,
          actionsRowTop: actionsRowRect.top,
          primaryRowBottom: primaryRowRect.bottom,
          actionsRowRight: actionsRowRect.right,
          clusterRight: clusterRect.right,
          clusterLeft: clusterRect.left,
          dateRight: dateRect.right,
          refreshLeft: refreshRect.left,
          refreshRight: refreshRect.right,
          closeLeft: closeRect.left,
          closeRight: closeRect.right,
          pathWidth: pathRect.width,
          toolbarWidth: toolbarRect.width,
          pathColor: getComputedStyle(path).color,
          textColor,
        };
        """
    )
    assert metrics["firstRowIsPrimary"]
    assert metrics["secondRowIsPath"]
    assert metrics["thirdRowIsActions"]
    assert metrics["noScopeRow"]
    assert metrics["noQuickAccessPanel"]
    assert metrics["modeFirstInPrimaryRow"]
    assert metrics["noPanelTitle"]
    assert metrics["actionsOrder"]
    assert metrics["folderIconPresent"]
    assert metrics["pathRowOrder"]
    assert metrics["hiddenBeforeSort"]
    assert metrics["syncText"] == "Sync"
    assert metrics["syncPressed"] == "true"
    assert metrics["rootPressedCount"] == 1
    assert metrics["diffSessionImmediatelyAfterMode"]
    assert metrics["spacerAfterDiffSession"]
    assert metrics["diffSessionVisibleInFilesMode"]
    assert metrics["noTopCollapseButton"]
    assert metrics["newFileRight"] <= metrics["newFolderLeft"]
    assert metrics["hiddenRight"] <= metrics["sortLeft"]
    assert metrics["syncRight"] <= metrics["pathLeft"]
    assert metrics["pathLeft"] > metrics["pathRowLeft"]
    assert metrics["pathWidth"] >= min(90, metrics["toolbarWidth"] / 4)
    assert metrics["pathRight"] <= metrics["copyLeft"]
    assert metrics["copyRight"] <= metrics["pathRowRight"] + 1
    assert metrics["modeRight"] <= metrics["diffSessionLeft"]
    assert metrics["diffSessionRight"] <= metrics["closeLeft"]
    assert metrics["modeButtonHorizontal"]
    assert metrics["modeLabelsHorizontal"]
    assert metrics["modeUsesTabFont"]
    assert metrics["modeButtonTopRounded"]
    assert metrics["activeModeUsesPaneTabColor"]
    assert metrics["modeButtonPaddingInline"] == ["3px/3px"]
    assert metrics["modeMaxButtonWidth"] <= 60
    assert metrics["pathConsumesRemaining"]
    assert metrics["modeTexts"] == ["Finder", "Differ", "Tabber"]
    assert abs(metrics["closeRight"] - metrics["primaryRowRight"]) <= 1
    assert metrics["pathColor"] == metrics["textColor"]
    assert metrics["pathRowTop"] >= metrics["primaryRowBottom"]
    assert metrics["actionsRowTop"] >= metrics["pathRowBottom"]
    assert metrics["dateRight"] <= metrics["refreshLeft"]
    assert metrics["refreshRight"] <= metrics["actionsRowRight"] + 1
    assert metrics["clusterLeft"] > metrics["pathLeft"]


def test_finder_diff_mode_toggle_fills_pane(browser, tmp_path):
    load_finder_click_toolbar_fixture(browser, tmp_path)
    before = browser.execute_script(
        """
        const filesButton = document.querySelector('[data-file-explorer-mode-set="files"]');
        const diffButton = document.querySelector('[data-file-explorer-mode-set="diff"]');
        const newFile = document.getElementById('new-file');
        const tree = document.querySelector('.file-explorer-tree-panel');
        const changes = document.querySelector('.file-explorer-changes-panel');
        return {
          bodyFiles: document.body.classList.contains('file-explorer-mode-files'),
          bodyDiff: document.body.classList.contains('file-explorer-mode-diff'),
          filesPressed: filesButton.getAttribute('aria-pressed'),
          diffPressed: diffButton.getAttribute('aria-pressed'),
          texts: Array.from(document.querySelectorAll('[data-file-explorer-mode-set]')).map(button => button.textContent.trim().replace(/\\s+/g, ' ')),
          diffButtonBg: getComputedStyle(diffButton).backgroundColor,
          newFileDisplay: getComputedStyle(newFile).display,
          treeDisplay: getComputedStyle(tree).display,
          changesDisplay: getComputedStyle(changes).display,
          titleCount: document.querySelectorAll('.file-explorer-panel-title').length,
        };
        """
    )
    assert before["bodyFiles"]
    assert not before["bodyDiff"]
    assert before["filesPressed"] == "true"
    assert before["diffPressed"] == "false"
    assert before["texts"] == ["Finder", "Differ", "Tabber"]
    assert before["newFileDisplay"] != "none"
    assert before["treeDisplay"] != "none"
    assert before["changesDisplay"] == "none"
    assert before["titleCount"] == 0

    browser.find_element("css selector", "[data-file-explorer-mode-set='diff']").click()
    after = browser.execute_script(
        """
        const filesButton = document.querySelector('[data-file-explorer-mode-set="files"]');
        const diffButton = document.querySelector('[data-file-explorer-mode-set="diff"]');
        const newFile = document.getElementById('new-file');
        const pane = document.querySelector('.file-explorer-pane');
        const tree = document.querySelector('.file-explorer-tree-panel');
        const changes = document.querySelector('.file-explorer-changes-panel');
        const visible = selector => Array.from(document.querySelectorAll(selector)).filter(node => node.getClientRects().length > 0);
        const changesStyle = getComputedStyle(changes);
        const paneRect = pane.getBoundingClientRect();
        const changesRect = changes.getBoundingClientRect();
        return {
          bodyFiles: document.body.classList.contains('file-explorer-mode-files'),
          bodyDiff: document.body.classList.contains('file-explorer-mode-diff'),
          panelMode: document.getElementById('finder-panel').dataset.fileExplorerMode,
          filesPressed: filesButton.getAttribute('aria-pressed'),
          diffPressed: diffButton.getAttribute('aria-pressed'),
          texts: Array.from(document.querySelectorAll('[data-file-explorer-mode-set]')).map(button => button.textContent.trim().replace(/\\s+/g, ' ')),
          diffButtonBg: getComputedStyle(diffButton).backgroundColor,
          newFileDisplay: getComputedStyle(newFile).display,
          treeDisplay: getComputedStyle(tree).display,
          changesDisplay: changesStyle.display,
          changesFlexGrow: changesStyle.flexGrow,
          changesMaxBlockSize: changesStyle.maxBlockSize,
          paneHeight: paneRect.height,
          changesHeight: changesRect.height,
          titleCount: document.querySelectorAll('.file-explorer-panel-title').length,
          visibleRootControls: visible('.file-explorer-root-mode-toggle-panel').length,
          visibleSessionSelects: visible('[data-session-files-session]').length,
          visibleSortSelects: visible('[data-session-files-sort]').length,
          visibleDateButtons: visible('[data-file-explorer-tree-dates]').length,
          visibleReloadButtons: visible('[data-session-files-refresh], [data-file-explorer-refresh]').length,
        };
        """
    )
    assert not after["bodyFiles"]
    assert after["bodyDiff"]
    assert after["panelMode"] == "diff"
    assert after["filesPressed"] == "false"
    assert after["diffPressed"] == "true"
    assert after["texts"] == ["Finder", "Differ", "Tabber"]
    assert after["diffButtonBg"] != before["diffButtonBg"]
    assert after["newFileDisplay"] == "none"
    assert after["treeDisplay"] == "none"
    assert after["changesDisplay"] != "none"
    assert after["changesFlexGrow"] == "1"
    assert after["changesMaxBlockSize"] == "none"
    assert abs(after["changesHeight"] - after["paneHeight"]) <= 1
    assert after["titleCount"] == 0
    assert after["visibleRootControls"] == 0
    assert after["visibleSessionSelects"] == 1
    assert after["visibleSortSelects"] == 1
    assert after["visibleDateButtons"] == 1
    assert after["visibleReloadButtons"] == 1

    browser.find_element("css selector", "[data-file-explorer-mode-set='files']").click()
    restored = browser.execute_script(
        """
        const filesButton = document.querySelector('[data-file-explorer-mode-set="files"]');
        const diffButton = document.querySelector('[data-file-explorer-mode-set="diff"]');
        return {
          bodyFiles: document.body.classList.contains('file-explorer-mode-files'),
          bodyDiff: document.body.classList.contains('file-explorer-mode-diff'),
          filesPressed: filesButton.getAttribute('aria-pressed'),
          diffPressed: diffButton.getAttribute('aria-pressed'),
          treeDisplay: getComputedStyle(document.querySelector('.file-explorer-tree-panel')).display,
          changesDisplay: getComputedStyle(document.querySelector('.file-explorer-changes-panel')).display,
        };
        """
    )
    assert restored["bodyFiles"]
    assert not restored["bodyDiff"]
    assert restored["filesPressed"] == "true"
    assert restored["diffPressed"] == "false"
    assert restored["treeDisplay"] != "none"
    assert restored["changesDisplay"] == "none"


def test_platform_controls_use_pc_glyphs(browser, tmp_path):
    load_pc_controls_fixture(browser, tmp_path)
    assert browser.execute_script("return getComputedStyle(document.getElementById('hidden-pane-zoom')).display") == "none"
    assert browser.execute_script("return getComputedStyle(document.getElementById('tab-minimize'), '::after').display") == "none"
    assert browser.execute_script("return getComputedStyle(document.getElementById('finder-close'), '::after').display") != "none"
    assert browser.execute_script("return getComputedStyle(document.getElementById('editor-close'), '::after').display") != "none"
    assert browser.execute_script("return getComputedStyle(document.getElementById('pane-zoom'), '::after').display") != "none"
    assert browser.execute_script("return document.getElementById('editor-close').getBoundingClientRect().width") <= 24
    assert browser.execute_script("return document.getElementById('tab-minimize').getBoundingClientRect().width") >= 18
    assert browser.execute_script("return getComputedStyle(document.getElementById('collapsed-preferences')).display") == "none"
    assert browser.execute_script("return getComputedStyle(document.getElementById('working-yolo')).animationName") == "yolo-marker-rotate"
    assert browser.execute_script("return getComputedStyle(document.getElementById('working-yolo'), '::after').content") == "none"
    # working YO spins SLOWLY at the yolo_rotate_ms setting (20s), not a fast hardcoded value.
    assert browser.execute_script("return getComputedStyle(document.getElementById('working-yolo')).animationDuration") == "20s"
    # An idle (auto-on, NON-working) marker must be STATIC — no ambient rotation.
    assert browser.execute_script("return getComputedStyle(document.getElementById('idle-yolo')).animationName") == "none"
    triangle_sizes = browser.execute_script(
        """
        const root = document.documentElement;
        const collapsed = getComputedStyle(document.querySelector('#collapsed-dir > .file-tree-icon'));
        const expanded = getComputedStyle(document.querySelector('#expanded-dir > .file-tree-icon'));
        const defaultWidth = document.querySelector('#collapsed-dir > .file-tree-icon').getBoundingClientRect().width;
        const defaultFontSize = Number.parseFloat(collapsed.fontSize);
        root.style.setProperty('--file-explorer-font-size', '8px');
        const smallIcon = document.querySelector('#collapsed-dir > .file-tree-icon');
        const smallStyle = getComputedStyle(smallIcon);
        const smallWidth = smallIcon.getBoundingClientRect().width;
        const smallFontSize = Number.parseFloat(smallStyle.fontSize);
        root.style.removeProperty('--file-explorer-font-size');
        return {
          collapsedSize: Number.parseFloat(collapsed.fontSize),
          expandedSize: Number.parseFloat(expanded.fontSize),
          collapsedWidth: defaultWidth,
          defaultFontSize,
          smallWidth,
          smallFontSize,
          expandedColor: expanded.color,
          collapsedColor: collapsed.color,
        };
        """
    )
    assert triangle_sizes["collapsedSize"] > 0
    assert triangle_sizes["expandedSize"] > 0
    assert triangle_sizes["smallWidth"] < triangle_sizes["collapsedWidth"]
    assert triangle_sizes["smallFontSize"] < triangle_sizes["defaultFontSize"]
    assert triangle_sizes["expandedColor"] != triangle_sizes["collapsedColor"]
    dots_center_delta = browser.execute_script(
        """
        const button = document.getElementById('pane-actions').getBoundingClientRect();
        const dots = document.getElementById('pane-actions-dots').getBoundingClientRect();
        const actionsStyle = getComputedStyle(document.getElementById('pane-actions'));
        const dotsStyle = getComputedStyle(document.getElementById('pane-actions-dots'));
        const hashStyle = getComputedStyle(document.getElementById('hash-tab'));
        return {
          x: Math.abs((button.left + button.width / 2) - (dots.left + dots.width / 2)),
          y: Math.abs((button.top + button.height / 2) - (dots.top + dots.height / 2)),
          background: actionsStyle.backgroundColor,
          borderColor: actionsStyle.borderTopColor,
          dotsColor: dotsStyle.color,
          hashColor: hashStyle.color,
        };
        """
    )
    assert dots_center_delta["x"] <= 1
    assert dots_center_delta["y"] <= 1
    assert dots_center_delta["background"] != "rgba(0, 0, 0, 0)"
    assert dots_center_delta["borderColor"] != "rgba(0, 0, 0, 0)"
    # Shared pane-chrome treatment: the "..." actions dots and the "#" control share ONE foreground color
    # (--pane-ctl-fg) now — consistent, not per-button (image 009).
    assert dots_center_delta["dotsColor"] == dots_center_delta["hashColor"]
    light_control = browser.execute_script(
        """
        document.body.classList.add('theme-light');
        const actionsStyle = getComputedStyle(document.getElementById('pane-actions'));
        const closeStyle = getComputedStyle(document.getElementById('finder-close'));
        return {
          actionsColor: actionsStyle.color,
          actionsBg: actionsStyle.backgroundColor,
          closeColor: closeStyle.color,
          closeBg: closeStyle.backgroundColor,
          infoLabelColor: getComputedStyle(document.querySelector('#info-tab .pane-tab-info-label')).color,
          infoTabBg: getComputedStyle(document.getElementById('info-tab')).backgroundColor,
        };
        """
    )
    assert light_control["actionsColor"] == "rgb(31, 41, 55)"
    assert light_control["actionsColor"] != light_control["actionsBg"]
    assert light_control["closeColor"] == "rgb(31, 41, 55)"
    assert light_control["closeColor"] != light_control["closeBg"]
    # the YO!info tab label is legible in light mode (color contrasts with the tab bg,
    # not white-on-white) now that it uses the themed .session-button-dir treatment.
    assert light_control["infoLabelColor"] != light_control["infoTabBg"]
    z_indexes = browser.execute_script(
        """
        return {
          contextMenu: Number.parseInt(getComputedStyle(document.getElementById('test-context-menu')).zIndex, 10),
          imagePreview: Number.parseInt(getComputedStyle(document.getElementById('test-image-preview')).zIndex, 10),
          tabPopover: Number.parseInt(getComputedStyle(document.getElementById('test-tab-popover')).zIndex, 10),
        };
        """
    )
    assert z_indexes["contextMenu"] > z_indexes["imagePreview"]
    assert z_indexes["contextMenu"] > z_indexes["tabPopover"]

    ActionChains(browser).move_to_element(browser.find_element("id", "tab-minimize")).perform()
    assert browser.execute_script("return getComputedStyle(document.getElementById('tab-minimize')).opacity") == "1"

    ActionChains(browser).move_to_element(browser.find_element("id", "pane-zoom")).perform()
    assert browser.execute_script("return getComputedStyle(document.getElementById('pane-zoom')).backgroundColor") != "rgba(0, 0, 0, 0)"

    ActionChains(browser).move_to_element(browser.find_element("id", "finder-close")).perform()
    assert browser.execute_script("return getComputedStyle(document.getElementById('finder-close')).opacity") == "1"

    ActionChains(browser).move_to_element(browser.find_element("id", "editor-close")).perform()
    assert browser.execute_script("return getComputedStyle(document.getElementById('editor-close')).opacity") == "1"

    tree_metrics = browser.execute_script(
        """
        const collapsedIcon = document.querySelector('#collapsed-dir .file-tree-icon');
        const expandedIcon = document.querySelector('#expanded-dir .file-tree-icon');
        const collapsedName = document.querySelector('#collapsed-dir .file-tree-name');
        return {
          collapsedColor: getComputedStyle(collapsedIcon).color,
          expandedColor: getComputedStyle(expandedIcon).color,
          iconSize: Number.parseFloat(getComputedStyle(collapsedIcon).fontSize),
          nameSize: Number.parseFloat(getComputedStyle(collapsedName).fontSize),
        };
        """
    )
    assert tree_metrics["collapsedColor"] != tree_metrics["expandedColor"]
    assert tree_metrics["iconSize"] > tree_metrics["nameSize"]
    repo_row_metrics = browser.execute_script(
        """
        const name = document.querySelector('#repo-dir .file-tree-name');
        const branch = document.querySelector('#repo-dir .file-tree-repo-branch');
        const diff = document.querySelector('#repo-dir .file-tree-diff');
        const add = document.querySelector('#repo-dir .changes-diff-add');
        const remove = document.querySelector('#repo-dir .changes-diff-remove');
        const nameRect = name.getBoundingClientRect();
        const diffRect = diff.getBoundingClientRect();
        const addRect = add.getBoundingClientRect();
        const removeRect = remove.getBoundingClientRect();
        return {
          text: name.textContent,
          hasRetiredDelta: Boolean(document.querySelector('#repo-dir .file-tree-repo-delta')),
          nameWeight: getComputedStyle(name).fontWeight,
          branchWeight: getComputedStyle(branch).fontWeight,
          diffWeight: getComputedStyle(diff).fontWeight,
          branchFont: getComputedStyle(branch).fontFamily,
          diffDisplay: getComputedStyle(diff).display,
          diffJustify: getComputedStyle(diff).justifyContent,
          addText: add.textContent,
          removeText: remove.textContent,
          addColor: getComputedStyle(add).color,
          removeColor: getComputedStyle(remove).color,
          diffRight: diffRect.right,
          addLeft: addRect.left,
          removeRight: removeRect.right,
          diffAfterName: diffRect.left >= nameRect.right,
          nameColor: getComputedStyle(name).color,
        };
        """
    )
    assert repo_row_metrics["text"] == "yolomux [feature/repo-row]"
    assert not repo_row_metrics["hasRetiredDelta"]
    assert repo_row_metrics["nameWeight"] in ("400", "normal")
    assert repo_row_metrics["branchWeight"] in ("400", "normal")
    assert repo_row_metrics["diffWeight"] == "800"
    assert "mono" in repo_row_metrics["branchFont"].lower()
    assert repo_row_metrics["diffDisplay"] == "flex"
    assert repo_row_metrics["diffJustify"] == "flex-end"
    assert repo_row_metrics["addText"] == "+5"
    assert repo_row_metrics["removeText"] == "-3"
    assert repo_row_metrics["addColor"] != repo_row_metrics["removeColor"]
    assert abs(repo_row_metrics["removeRight"] - repo_row_metrics["diffRight"]) <= 1
    assert repo_row_metrics["diffAfterName"]
    assert repo_row_metrics["nameColor"] != tree_metrics["collapsedColor"]


def test_editor_pane_does_not_shift_grid_when_legacy_body_class_is_present(browser, tmp_path):
    load_editor_pane_legacy_body_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const grid = document.getElementById('grid');
        const gridStyle = getComputedStyle(grid);
        const panel = document.querySelector('.file-editor-panel').getBoundingClientRect();
        return {
          paddingLeft: Number.parseFloat(gridStyle.paddingLeft),
          panelLeft: panel.left,
        };
        """
    )
    assert metrics["paddingLeft"] <= 10
    assert metrics["panelLeft"] <= 16


def test_codemirror_editor_controls_are_sized_and_aligned(browser, tmp_path):
    load_codemirror_editor_controls_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const firstTab = document.querySelector('.pane-tab').getBoundingClientRect();
        const actions = document.getElementById('editor-actions').getBoundingClientRect();
        const search = document.getElementById('search-field').getBoundingClientRect();
        const replace = document.getElementById('replace-field').getBoundingClientRect();
        const nextButton = document.querySelector('.cm-button[name="next"]').getBoundingClientRect();
        const previousButton = document.querySelector('.cm-button[name="prev"]').getBoundingClientRect();
        const allButton = document.querySelector('.cm-button[name="select"]').getBoundingClientRect();
        const replaceButton = document.querySelector('.cm-button[name="replace"]').getBoundingClientRect();
        const replaceAllButton = document.querySelector('.cm-button[name="replaceAll"]').getBoundingClientRect();
        const count = document.getElementById('search-count').getBoundingClientRect();
        const label = document.getElementById('match-label').getBoundingClientRect();
        const regexpLabel = document.querySelectorAll('.cm-search label')[1].getBoundingClientRect();
        const wordLabel = document.querySelectorAll('.cm-search label')[2].getBoundingClientRect();
        const labelStyle = getComputedStyle(document.getElementById('match-label'));
        const checkbox = document.getElementById('match-case').getBoundingClientRect();
        const markerContent = getComputedStyle(document.getElementById('wrapped-line'), '::before').content;
        const marker = document.getElementById('wrap-marker').getBoundingClientRect();
        const markerStyle = getComputedStyle(document.getElementById('wrap-marker'));
        const panelRing = getComputedStyle(document.querySelector('.file-editor-panel'));
        const searchLabel = getComputedStyle(document.querySelector('.cm-search'), '::before').content;
        const editorStyle = getComputedStyle(document.getElementById('cm-editor'));
        const themeStyle = getComputedStyle(document.querySelector('.file-editor-theme-panel'));
        const wrapStyle = getComputedStyle(document.querySelector('.file-editor-wrap-panel'));
        const findStyle = getComputedStyle(document.querySelector('.file-editor-find-panel'));
        const previewStyle = getComputedStyle(document.querySelector('[data-editor-mode="preview"]'));
        const closeStyle = getComputedStyle(document.querySelector('.file-editor-panel-close'));
        const searchCloseStyle = getComputedStyle(document.querySelector('.cm-dialog-close'));
        const syntaxProbe = Array.from(document.querySelectorAll('#light-syntax-probe span')).map(node => {
          const style = getComputedStyle(node);
          return {color: style.color, background: style.backgroundColor, border: style.borderTopColor};
        });
        const filePopoverStyle = getComputedStyle(document.getElementById('file-popover'));
        const filePopoverCopyStyle = getComputedStyle(document.getElementById('file-popover-copy'));
        const findControl = document.querySelector('.file-editor-find-panel').getBoundingClientRect();
        const wrapControl = document.querySelector('.file-editor-wrap-panel').getBoundingClientRect();
        const modeControl = document.querySelector('[data-editor-mode="preview"]').getBoundingClientRect();
        const modeButtonRects = Array.from(document.querySelectorAll('.file-editor-mode-control button')).map(button => button.getBoundingClientRect());
        const toolbarButtons = Array.from(document.querySelectorAll([
          '.file-editor-gutter-panel',
          '.file-editor-wrap-panel',
          '.file-editor-find-panel',
          '.file-editor-blame-panel',
          '.file-editor-diff-panel',
          '.file-editor-diff-expand-panel',
          '.file-editor-theme-panel',
          '.file-editor-reload-panel',
          '.file-editor-save-panel',
        ].join(',')));
        const modeIconDeltas = Array.from(document.querySelectorAll('.file-editor-mode-control button')).map(button => {
          const buttonRect = button.getBoundingClientRect();
          const iconRect = button.querySelector('.file-editor-icon').getBoundingClientRect();
          return Math.abs((buttonRect.top + buttonRect.height / 2) - (iconRect.top + iconRect.height / 2));
        });
        const toolbarIconDeltas = toolbarButtons
          .filter(button => button.querySelector('.file-editor-icon'))
          .map(button => {
            const buttonRect = button.getBoundingClientRect();
            const iconRect = button.querySelector('.file-editor-icon').getBoundingClientRect();
            return {
              cls: button.className,
              dx: Math.abs((buttonRect.left + buttonRect.width / 2) - (iconRect.left + iconRect.width / 2)),
              dy: Math.abs((buttonRect.top + buttonRect.height / 2) - (iconRect.top + iconRect.height / 2)),
            };
          });
        const toolbarButtonRects = toolbarButtons.map(button => button.getBoundingClientRect());
        const elementAtCenter = rect => document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        const tabRows = [];
        for (const tab of Array.from(document.querySelectorAll('.pane-tab'))) {
          const rect = tab.getBoundingClientRect();
          let row = tabRows.find(item => Math.abs(item.top - rect.top) <= 1);
          if (!row) {
            row = {top: rect.top, rights: []};
            tabRows.push(row);
          }
          row.rights.push(rect.right);
        }
        return {
          actionsTopDelta: Math.abs(actions.top - firstTab.top),
          searchWidth: search.width,
          replaceWidth: replace.width,
          nextWidth: nextButton.width,
          previousWidth: previousButton.width,
          allWidth: allButton.width,
          countText: document.getElementById('search-count').textContent,
          countColor: getComputedStyle(document.getElementById('search-count')).color,
          nextTitle: document.querySelector('.cm-button[name="next"]').title,
          previousTitle: document.querySelector('.cm-button[name="prev"]').title,
          searchFirstToggleGap: label.left - search.right,
          toggleCountGap: count.left - regexpLabel.right,
          previousNextGap: nextButton.left - previousButton.right,
          nextAllGap: allButton.left - nextButton.right,
          replaceReplaceAllGap: replaceAllButton.left - replaceButton.right,
          labelRegexpGap: wordLabel.left - label.right,
          regexpWordGap: regexpLabel.left - wordLabel.right,
          replaceLeftDelta: Math.abs(search.left - replace.left),
          replaceWidthDelta: Math.abs(search.width - replace.width),
          checkboxCenterDelta: Math.abs((checkbox.top + checkbox.height / 2) - (label.top + label.height / 2)),
          labelFontFamily: labelStyle.fontFamily,
          labelFontSize: Number.parseFloat(labelStyle.fontSize),
          markerContent,
          markerHeight: marker.height,
          markerColor: markerStyle.color,
          // the focus ring is the translucent gutter border (color-mix of --panel-ring-color).
          panelRingBorderColor: getComputedStyle(document.querySelector('.file-editor-panel')).borderTopColor,
          searchLabel,
          editorBg: editorStyle.backgroundColor,
          editorColor: editorStyle.color,
          themeBg: themeStyle.backgroundColor,
          themeBorderColor: themeStyle.borderTopColor,
          themeColor: themeStyle.color,
          wrapBg: wrapStyle.backgroundColor,
          wrapBorderColor: wrapStyle.borderTopColor,
          findBg: findStyle.backgroundColor,
          previewBg: previewStyle.backgroundColor,
          closeBg: closeStyle.backgroundColor,
          searchCloseColor: searchCloseStyle.color,
          searchCloseBg: searchCloseStyle.backgroundColor,
          syntaxColorCount: new Set(syntaxProbe.map(item => item.color)).size,
          keywordColor: syntaxProbe[0].color,
          stringColor: syntaxProbe[1].color,
          functionColor: syntaxProbe[3].color,
          commentColor: syntaxProbe[4].color,
          headingColor: syntaxProbe[5].color,
          inlineCodeColor: syntaxProbe[6].color,
          inlineCodeBg: syntaxProbe[6].background,
          inlineCodeBorder: syntaxProbe[6].border,
          listMarkerColor: syntaxProbe[7].color,
          linkColor: syntaxProbe[8].color,
          filePopoverPointerEvents: filePopoverStyle.pointerEvents,
          filePopoverCopyPointerEvents: filePopoverCopyStyle.pointerEvents,
          findControlClickable: Boolean(elementAtCenter(findControl)?.closest?.('.file-editor-find-panel')),
          wrapControlClickable: Boolean(elementAtCenter(wrapControl)?.closest?.('.file-editor-wrap-panel')),
          previewControlClickable: Boolean(elementAtCenter(modeControl)?.closest?.('[data-editor-mode="preview"]')),
          modeButtonTopSpread: Math.max(...modeButtonRects.map(rect => rect.top)) - Math.min(...modeButtonRects.map(rect => rect.top)),
          modeButtonHeightSpread: Math.max(...modeButtonRects.map(rect => rect.height)) - Math.min(...modeButtonRects.map(rect => rect.height)),
          modeIconCenterMaxDelta: Math.max(...modeIconDeltas),
          toolbarButtonTopSpread: Math.max(...toolbarButtonRects.map(rect => rect.top)) - Math.min(...toolbarButtonRects.map(rect => rect.top)),
          toolbarButtonHeightSpread: Math.max(...toolbarButtonRects.map(rect => rect.height)) - Math.min(...toolbarButtonRects.map(rect => rect.height)),
          toolbarIconCenterMaxDx: Math.max(...toolbarIconDeltas.map(item => item.dx)),
          toolbarIconCenterMaxDy: Math.max(...toolbarIconDeltas.map(item => item.dy)),
          toolbarIconDeltas,
          tabRowCount: tabRows.length,
          lowerTabRowsUseFullWidth: tabRows.slice(1).some(row => Math.max(...row.rights) > actions.left + 20),
        };
        """
    )
    assert metrics["actionsTopDelta"] <= 2
    assert metrics["searchWidth"] >= 120
    assert metrics["searchWidth"] <= 210
    assert metrics["replaceWidth"] >= 120
    assert metrics["nextWidth"] <= 45
    assert metrics["previousWidth"] <= 75
    assert metrics["allWidth"] <= 38
    assert metrics["countText"] == "3/102"
    assert metrics["countColor"] != "rgb(0, 0, 0)"
    assert metrics["nextTitle"] == "Next match (Enter)"
    assert metrics["previousTitle"] == "Previous match (Shift+Enter)"
    assert 0 <= metrics["searchFirstToggleGap"] <= 8
    assert 0 <= metrics["toggleCountGap"] <= 10
    assert metrics["previousNextGap"] <= 6
    assert metrics["nextAllGap"] <= 6
    assert metrics["replaceReplaceAllGap"] <= 4
    assert metrics["labelRegexpGap"] <= 4
    assert metrics["regexpWordGap"] <= 4
    assert metrics["replaceLeftDelta"] <= 1.5
    assert metrics["replaceWidthDelta"] <= 2
    assert metrics["checkboxCenterDelta"] <= 1.5
    assert (
        "Arial Narrow" in metrics["labelFontFamily"]
        or "Roboto Condensed" in metrics["labelFontFamily"]
        or metrics["labelFontSize"] <= 11
    )
    assert metrics["markerContent"] in ("none", '""')
    assert metrics["markerHeight"] > 0
    assert metrics["markerColor"] != "rgb(0, 0, 0)"
    # the active pane's focus ring is the translucent gutter border; assert it shows a
    # colored (non-transparent) ring color (color-mix of --panel-ring-color at --pane-ring-opacity).
    assert metrics["panelRingBorderColor"] not in ("rgba(0, 0, 0, 0)", "transparent")
    assert metrics["searchLabel"] in ("none", '""')
    assert metrics["editorBg"] != "rgb(15, 17, 21)"
    assert metrics["editorColor"] != "rgb(228, 232, 238)"
    assert metrics["themeBorderColor"] != "rgba(0, 0, 0, 0)"
    assert metrics["themeColor"] != metrics["editorColor"]
    assert metrics["wrapBorderColor"] != "rgba(0, 0, 0, 0)"
    assert metrics["themeBg"] != "rgba(0, 0, 0, 0)"
    assert metrics["wrapBg"] not in ("rgb(255, 255, 255)", "rgb(221, 244, 255)")
    assert metrics["findBg"] != "rgba(0, 0, 0, 0)"
    assert metrics["previewBg"] != "rgba(0, 0, 0, 0)"
    assert metrics["wrapBg"] != metrics["findBg"]
    assert metrics["closeBg"] != metrics["editorBg"]
    assert metrics["closeBg"] != "rgb(255, 235, 233)"
    assert metrics["searchCloseColor"] != "rgb(255, 255, 255)"
    assert metrics["searchCloseColor"] != metrics["searchCloseBg"]
    assert metrics["syntaxColorCount"] >= 6
    assert metrics["keywordColor"] != metrics["stringColor"]
    assert metrics["functionColor"] != metrics["keywordColor"]
    assert metrics["inlineCodeColor"] != metrics["headingColor"]
    assert metrics["inlineCodeColor"] != metrics["linkColor"]
    assert metrics["inlineCodeColor"] != metrics["listMarkerColor"]
    assert metrics["headingColor"] != metrics["linkColor"]
    assert metrics["commentColor"] == metrics["listMarkerColor"]
    assert metrics["inlineCodeBg"] != "rgba(0, 0, 0, 0)"
    assert metrics["inlineCodeBorder"] != "rgba(0, 0, 0, 0)"
    assert metrics["filePopoverPointerEvents"] == "auto"  # popover-open tab: interactive when visible
    assert metrics["filePopoverCopyPointerEvents"] == "auto"
    assert metrics["findControlClickable"]
    assert metrics["wrapControlClickable"]
    assert metrics["previewControlClickable"]
    assert metrics["modeButtonTopSpread"] <= 1
    assert metrics["modeButtonHeightSpread"] <= 1
    assert metrics["modeIconCenterMaxDelta"] <= 1.5
    assert metrics["toolbarButtonTopSpread"] <= 1
    assert metrics["toolbarButtonHeightSpread"] <= 1
    assert metrics["toolbarIconCenterMaxDx"] <= 1.5, metrics["toolbarIconDeltas"]
    assert metrics["toolbarIconCenterMaxDy"] <= 1.5, metrics["toolbarIconDeltas"]


def test_editor_diff_ref_reset_is_visible_and_hittable(browser, tmp_path):
    load_editor_diff_ref_toolbar_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const toolbar = document.querySelector('.file-editor-toolbar').getBoundingClientRect();
        const leftZone = document.querySelector('.file-editor-toolbar-left').getBoundingClientRect();
        const centerZone = document.querySelector('.file-editor-toolbar-center').getBoundingClientRect();
        const rightZone = document.querySelector('.file-editor-toolbar-right').getBoundingClientRect();
        const gutter = document.getElementById('gutter-button').getBoundingClientRect();
        const wrap = document.getElementById('wrap-button').getBoundingClientRect();
        const wrapIcon = document.querySelector('#wrap-button .file-editor-icon-wrap');
        const diff = document.getElementById('diff-button').getBoundingClientRect();
        const expand = document.getElementById('diff-expand-button').getBoundingClientRect();
        const font = document.getElementById('font-panel').getBoundingClientRect();
        const mode = document.getElementById('mode-control').getBoundingClientRect();
        const diffStyle = getComputedStyle(document.getElementById('diff-button'));
        const panel = document.getElementById('diff-ref-panel').getBoundingClientRect();
        const controls = document.querySelector('[data-diff-ref-controls]').getBoundingClientRect();
        const to = document.getElementById('to-ref').getBoundingClientRect();
        const reset = document.getElementById('reset-ref').getBoundingClientRect();
        const resetStyle = getComputedStyle(document.getElementById('reset-ref'));
        const panelStyle = getComputedStyle(document.getElementById('diff-ref-panel'));
        const hit = document.elementFromPoint(reset.left + reset.width / 2, reset.top + reset.height / 2);
        return {
          toolbarLeft: toolbar.left,
          toolbarRight: toolbar.right,
          toolbarCenter: toolbar.left + toolbar.width / 2,
          leftZoneLeft: leftZone.left,
          leftZoneRight: leftZone.right,
          centerZoneCenter: centerZone.left + centerZone.width / 2,
          rightZoneLeft: rightZone.left,
          rightZoneRight: rightZone.right,
          gutterLeft: gutter.left,
          gutterRight: gutter.right,
          wrapLeft: wrap.left,
          wrapRight: wrap.right,
          wrapHasIcon: Boolean(wrapIcon),
          diffLeft: diff.left,
          diffRight: diff.right,
          diffText: document.getElementById('diff-button').textContent.trim(),
          expandLeft: expand.left,
          expandRight: expand.right,
          fontCenter: font.left + font.width / 2,
          modeLeft: mode.left,
          modeRight: mode.right,
          diffBg: diffStyle.backgroundColor,
          diffBorder: diffStyle.borderTopColor,
          panelRight: panel.right,
          panelLeft: panel.left,
          controlsRight: controls.right,
          toRight: to.right,
          resetLeft: reset.left,
          resetRight: reset.right,
          resetWidth: reset.width,
          resetDisplay: resetStyle.display,
          panelOverflow: panelStyle.overflow,
          hitReset: Boolean(hit?.closest?.('#reset-ref')),
          hitId: hit?.id || '',
          hitClass: String(hit?.className || ''),
          hitText: hit?.textContent || '',
        };
        """
    )
    assert metrics["leftZoneLeft"] <= metrics["toolbarLeft"] + 8, metrics
    assert abs(metrics["centerZoneCenter"] - metrics["toolbarCenter"]) <= 2, metrics
    assert abs(metrics["fontCenter"] - metrics["toolbarCenter"]) <= 2, metrics
    assert metrics["rightZoneRight"] >= metrics["toolbarRight"] - 8, metrics
    assert metrics["modeLeft"] >= metrics["centerZoneCenter"] + 20, metrics
    assert metrics["modeLeft"] >= metrics["leftZoneRight"], metrics
    assert metrics["gutterLeft"] <= metrics["toolbarLeft"] + 8, metrics
    assert 0 <= metrics["wrapLeft"] - metrics["gutterRight"] <= 6, metrics
    assert metrics["wrapHasIcon"], metrics
    assert 0 <= metrics["diffLeft"] - metrics["wrapRight"] <= 6, metrics
    assert metrics["diffText"] == "Differ", metrics
    assert 0 <= metrics["expandLeft"] - metrics["diffRight"] <= 6, metrics
    assert 0 <= metrics["panelLeft"] - metrics["expandRight"] <= 6, metrics
    assert metrics["diffBg"] != "rgba(0, 0, 0, 0)", metrics
    assert metrics["diffBorder"] != "rgba(0, 0, 0, 0)", metrics
    assert metrics["resetDisplay"] != "none"
    assert metrics["resetWidth"] >= 18
    assert metrics["panelOverflow"] == "visible"
    assert 0 <= metrics["resetLeft"] - metrics["toRight"] <= 5
    assert metrics["resetRight"] <= metrics["panelRight"] + 1
    assert metrics["controlsRight"] <= metrics["panelRight"] + 1
    assert metrics["panelRight"] <= metrics["toolbarRight"] + 1
    assert metrics["hitReset"], metrics


def test_codemirror_word_wrap_toggle_keeps_existing_content_visible(browser, tmp_path):
    load_codemirror_wrap_toggle_fixture(browser, tmp_path)
    ready = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        window.__wrapRegressionReady.then(
          () => done({ok: true}),
          error => done({ok: false, message: String(error)})
        );
        """
    )
    assert ready["ok"], ready
    before = browser.execute_script(
        """
        const panel = document.getElementById('wrap-regression-panel');
        const content = panel.querySelector('.cm-content');
        return {
          sameView: panel._cmView === window.__wrapRegressionInitialView,
          renderCalls: window.__wrapRegressionRenderCalls,
          doc: panel._cmView.state.doc.toString(),
          visibleText: content.textContent,
          lineWrapping: content.classList.contains('cm-lineWrapping'),
          buttonActive: panel.querySelector('.file-editor-wrap-panel').classList.contains('active'),
          contentHeight: content.getBoundingClientRect().height,
        };
        """
    )
    assert before["sameView"]
    assert before["renderCalls"] == 0
    assert "This line must stay visible" in before["doc"]
    assert "This line must stay visible" in before["visibleText"]
    assert before["lineWrapping"] is False
    assert before["buttonActive"] is False
    assert before["contentHeight"] > 0

    after = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const panel = document.getElementById('wrap-regression-panel');
        panel.querySelector('.file-editor-wrap-panel').click();
        let attempts = 0;
        const finish = () => {
          const content = panel.querySelector('.cm-content');
          const metrics = {
            sameView: panel._cmView === window.__wrapRegressionInitialView,
            renderCalls: window.__wrapRegressionRenderCalls,
            doc: panel._cmView.state.doc.toString(),
            visibleText: content.textContent,
            lineWrapping: Boolean(panel.querySelector('.cm-lineWrapping')),
            panelWrap: panel.classList.contains('editor-wrap'),
            buttonActive: panel.querySelector('.file-editor-wrap-panel').classList.contains('active'),
            contentHeight: content.getBoundingClientRect().height,
            editorClass: panel.querySelector('.cm-editor')?.className || '',
            scrollerClass: panel.querySelector('.cm-scroller')?.className || '',
            contentClass: content.className,
            contentWhiteSpace: getComputedStyle(content).whiteSpace,
            reconfigCalls: window.__wrapRegressionReconfigCalls,
            errors: window.__wrapRegressionErrors,
            optionViews: panel._cmEditorOptionViews?.length || 0,
            loadingText: panel.querySelector('.file-editor-codemirror-panel').textContent,
          };
          if (metrics.lineWrapping || attempts > 20) done(metrics);
          else {
            attempts += 1;
            requestAnimationFrame(finish);
          }
        };
        requestAnimationFrame(finish);
        """
    )
    assert after["sameView"], after
    assert after["renderCalls"] == 0, after
    assert "This line must stay visible" in after["doc"]
    assert "This line must stay visible" in after["visibleText"]
    assert after["lineWrapping"] is True, (
        f"contentClass={after['contentClass']} "
        f"contentWhiteSpace={after['contentWhiteSpace']} "
        f"reconfigCalls={after['reconfigCalls']} "
        f"errors={after['errors']} "
        f"optionViews={after['optionViews']}"
    )
    assert after["panelWrap"] is True
    assert after["buttonActive"] is True
    assert after["contentHeight"] > 0
    assert after["reconfigCalls"], after
    assert after["reconfigCalls"][-1]["result"] is True, after
    assert "cm-lineWrapping" in after["reconfigCalls"][-1]["classes"]
    assert after["errors"] == []
    assert "loading CodeMirror" not in after["loadingText"]


def test_codemirror_bundle_exports_decoration_for_html_semantic_marks(browser, tmp_path):
    load_codemirror_bundle_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const cm = window.YOLOmuxCodeMirror || {};
            return typeof cm.Decoration?.mark === 'function'
              && typeof cm.Decoration?.set === 'function'
              && typeof cm.MergeView === 'function'
              && typeof cm.unifiedMergeView === 'function';
            """
        )
    )
    metrics = browser.execute_script(
        """
        const cm = window.YOLOmuxCodeMirror || {};
        const mark = cm.Decoration?.mark?.({attributes: {style: 'font-weight:700'}});
        return {
          hasDecoration: typeof cm.Decoration?.mark === 'function',
          hasDecorationSet: typeof cm.Decoration?.set === 'function',
          hasMergeView: typeof cm.MergeView === 'function',
          hasUnifiedMergeView: typeof cm.unifiedMergeView === 'function',
          markWorks: Boolean(mark && typeof mark.range === 'function'),
        };
        """
    )
    assert metrics["hasDecoration"]
    assert metrics["hasDecorationSet"]
    assert metrics["hasMergeView"]
    assert metrics["hasUnifiedMergeView"]
    assert metrics["markWorks"]


def test_clicking_finder_does_not_change_terminal_pane_toolbar(browser, tmp_path):
    load_finder_click_toolbar_fixture(browser, tmp_path)
    light_metrics = browser.execute_script(
        """
        document.body.classList.add('theme-light');
        const detail = document.querySelector('#terminal-panel .panel-detail-row');
        const meta = detail.querySelector('.meta');
        const action = document.querySelector('#terminal-panel .pane-actions');
        return {
          detailBg: getComputedStyle(detail).backgroundColor,
          metaColor: getComputedStyle(meta).color,
          actionColor: getComputedStyle(action).color,
          actionBg: getComputedStyle(action).backgroundColor,
        };
        """
    )
    # The detail row is the tinted (active-accent-derived) chrome strip with readable dark meta text —
    # assert the readability relationship, not a pinned green, so the active_color picker doesn't break it.
    assert light_metrics["detailBg"] != light_metrics["metaColor"]
    assert light_metrics["metaColor"] == "rgb(31, 41, 55)"
    assert light_metrics["actionColor"] == "rgb(31, 41, 55)"
    assert light_metrics["actionColor"] != light_metrics["actionBg"]
    before = browser.execute_script(
        """
        const toolbar = document.getElementById('terminal-toolbar');
        const rect = toolbar.getBoundingClientRect();
        return {
          html: toolbar.innerHTML,
          display: getComputedStyle(toolbar).display,
          buttonCount: toolbar.querySelectorAll('.tab').length,
          left: rect.left,
          width: rect.width,
        };
        """
    )
    browser.find_element("id", "finder-panel").click()
    after = browser.execute_script(
        """
        const toolbar = document.getElementById('terminal-toolbar');
        const rect = toolbar.getBoundingClientRect();
        return {
          html: toolbar.innerHTML,
          display: getComputedStyle(toolbar).display,
          buttonCount: toolbar.querySelectorAll('.tab').length,
          left: rect.left,
          width: rect.width,
        };
        """
    )
    assert after == before


# — light-mode surface regression guard. The recurring light-mode bug class is a
# component rule that hardcodes a DARK color literal with no body.theme-light / body.editor-theme-light
# counterpart, so it renders as a dark box (or invisible pale text) on the white surface. The earlier
# white-on-white miss slipped through because the test measured BACKGROUNDS but never the nested TEXT
# color. This builds each fixed surface in light mode and asserts (a) container backgrounds are LIGHT
# and (b) text vs its surface meets a real contrast ratio — the same thing a human reading it needs.
LIGHT_MODE_SURFACES = """
<div class="command-palette-dialog" id="cp-dlg">
  <input class="command-palette-input" id="cp-inp" value="x">
  <button class="command-palette-row active" id="cp-row">
    <span class="command-palette-group" id="cp-grp">FILES</span>
    <span class="command-palette-detail" id="cp-det">detail</span>
    <span class="command-palette-keybinding" id="cp-kb">^P</span>
  </button>
</div>
<div class="keyboard-shortcuts-dialog" id="ks-dlg">
  <div class="keyboard-shortcut-row"><span>act</span><kbd id="ks-kbd">Ctrl</kbd></div>
</div>
<div class="preferences-global-reset" id="gr">
  <div class="preferences-global-reset-title" id="gr-title">Reset</div>
  <div class="preferences-global-reset-warning" id="gr-warn">warn</div>
</div>
<span class="agent-icon codex" id="agent-ico">A</span>
<span class="session-state-badge" id="badge-neutral">run</span>
<span class="session-state-badge session-state-done" id="badge-done">done</span>
<span class="session-yolo-marker inactive" id="ym-inactive">YO</span>
<button class="pane-tab file-missing" id="fm-tab">
  <span class="session-button-dir" id="fm-dir">gone</span>
  <span class="file-tab-missing-badge" id="fm-badge">!</span>
</button>
<div class="server-update-banner" id="sub">
  update <button class="server-update-banner-dismiss" id="sub-dismiss">x</button>
</div>
<div class="file-tree-row repo-non-main"><span class="file-tree-name" id="rnm-name">repo</span></div>
<div class="file-tree-row indexed-directory">
  <span class="file-tree-name" id="idx-name">dir</span>
  <span class="file-tree-git-status" id="idx-status">INDEXED</span>
</div>
<input class="file-tree-rename-input" id="rename-inp" value="name">
<div class="yoagent-message-body markdown-body"><pre id="md-pre"><code>code</code></pre></div>
<div class="info-pane" style="background:var(--bg)">
  <div class="info-row header"><div class="info-cell" id="info-hdr">Session</div></div>
  <div class="info-row"><div class="info-cell" id="info-row-text">main</div>
    <div class="info-cell"><a id="info-link" href="#">branch</a></div></div>
  <div class="info-row current"><div class="info-cell" id="info-cur">current</div></div>
</div>
"""


def light_mode_surfaces_fixture_html(body_class):
    css = app_css()
    return f"""
    <!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head>
    <body class="{body_class}" style="background:#fff">{LIGHT_MODE_SURFACES}</body></html>
    """


def _contrast_ratio(rgb_a, rgb_b):
    def rel_lum(css_rgb):
        nums = [int(n) for n in re.findall(r"\d+", css_rgb)[:3]]

        def chan(c):
            c = c / 255.0
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        return 0.2126 * chan(nums[0]) + 0.7152 * chan(nums[1]) + 0.0722 * chan(nums[2])

    la, lb = rel_lum(rgb_a), rel_lum(rgb_b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_light_mode_surfaces_are_readable_not_dark_boxes(browser, tmp_path):
    page = tmp_path / "light-surfaces.html"
    page.write_text(light_mode_surfaces_fixture_html("theme-light"), encoding="utf-8")
    browser.get(page.as_uri())
    style = browser.execute_script(
        """
        const out = {};
        for (const el of document.querySelectorAll('[id]')) {
          const s = getComputedStyle(el);
          out[el.id] = {color: s.color, bg: s.backgroundColor};
        }
        return out;
        """
    )

    # (a) Surfaces that were dark boxes must now have LIGHT backgrounds (luminance high).
    def _lum(css_rgb):
        nums = [int(n) for n in re.findall(r"\d+", css_rgb)[:3]]
        return 0.2126 * nums[0] + 0.7152 * nums[1] + 0.0722 * nums[2]

    for box in ("cp-dlg", "ks-dlg", "sub", "rename-inp", "md-pre"):
        assert _lum(style[box]["bg"]) > 180, f"{box} background must be light in light mode, got {style[box]['bg']}"

    # (b) Text must contrast with its surface. Where the element bg is transparent, it sits on the white page.
    page_white = "rgb(255, 255, 255)"
    text_checks = {
        "cp-row": "cp-dlg", "cp-grp": "cp-dlg", "cp-det": "cp-dlg", "cp-kb": "cp-dlg",
        "ks-kbd": "ks-kbd", "gr-title": "gr", "gr-warn": "gr", "agent-ico": None,
        "badge-neutral": "badge-neutral", "badge-done": "badge-done", "ym-inactive": "ym-inactive",
        "fm-dir": "fm-tab", "fm-badge": "fm-tab", "sub": "sub", "sub-dismiss": "sub",
        "rnm-name": None, "idx-name": None, "idx-status": None, "rename-inp": "rename-inp", "md-pre": "md-pre",
        # the YO!info table — rows/header/current/links must read on the light pane.
        "info-hdr": None, "info-row-text": None, "info-link": None, "info-cur": None,
    }
    for eid, bg_id in text_checks.items():
        bg = style[bg_id]["bg"] if bg_id else page_white
        if "rgba(0, 0, 0, 0)" in bg or bg == "transparent":
            bg = page_white
        ratio = _contrast_ratio(style[eid]["color"], bg)
        assert ratio >= 3.0, f"{eid}: text {style[eid]['color']} on {bg} contrast {ratio:.1f} < 3.0 (dark-box/invisible)"


def test_light_editor_image_backdrop_is_light(browser, tmp_path):
    page = tmp_path / "light-editor-image.html"
    page.write_text(
        light_mode_surfaces_fixture_html("editor-theme-light").replace(
            LIGHT_MODE_SURFACES,
            '<div class="file-editor-image-panel" id="imgp"><img class="file-editor-image" id="img" src="#"></div>',
        ),
        encoding="utf-8",
    )
    browser.get(page.as_uri())
    style = browser.execute_script(
        "return {panel: getComputedStyle(document.getElementById('imgp')).backgroundColor,"
        " img: getComputedStyle(document.getElementById('img')).backgroundColor};"
    )

    def _lum(css_rgb):
        nums = [int(n) for n in re.findall(r"\d+", css_rgb)[:3]]
        return 0.2126 * nums[0] + 0.7152 * nums[1] + 0.0722 * nums[2]

    assert _lum(style["panel"]) > 180, f"editor-light image panel must be light, got {style['panel']}"
    assert _lum(style["img"]) > 180, f"editor-light image backdrop must be light, got {style['img']}"


def codemirror_search_panel_fixture_html():
    css = app_css()
    bundle_uri = (REPO_ROOT / "static" / "codemirror.js").as_uri()
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
        <script src="{bundle_uri}"></script>
        <style>.file-editor-codemirror {{ width: 680px; height: 220px; }}</style>
      </head>
      <body class="editor-theme-light">
        <div class="panel file-editor-panel active-pane">
          <div class="file-editor-content file-editor-codemirror" id="cm-host"></div>
        </div>
        <script>
          (function() {{
            const CM = window.YOLOmuxCodeMirror;
            const exts = CM.search ? [CM.search()] : [];
            const view = new CM.EditorView({{
              state: CM.EditorState.create({{doc: "hello world\\nfind me\\n", extensions: exts}}),
              parent: document.getElementById('cm-host'),
            }});
            CM.openSearchPanel(view);
          }})();
        </script>
      </body>
    </html>
    """


def load_codemirror_search_panel_fixture(browser, tmp_path):
    page = tmp_path / "cm-search-panel.html"
    page.write_text(codemirror_search_panel_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


def test_codemirror_search_toggle_labels_collapse_to_glyph_not_overflow(browser, tmp_path):
    # CodeMirror's baseTheme injects `.cm-panel.cm-search label { font-size: 80% }` at RUNTIME, a
    # specificity TIE with our label rule that wins on source order — un-hiding the native toggle
    # text ("match case"/"regexp"/"by word") so it overflows the 24px box and collides with our
    # compact ::after glyph (images 019/021). The +1-class override must keep the label font-size 0.
    load_codemirror_search_panel_fixture(browser, tmp_path)
    labels = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const panel = document.querySelector('.cm-search');
            if (!panel) return false;
            const labels = [...panel.querySelectorAll('label')].map(l => ({
              fontSize: getComputedStyle(l).fontSize,
              boxWidth: Math.round(l.getBoundingClientRect().width),
              scrollWidth: l.scrollWidth,
            }));
            return labels.length ? labels : false;
            """
        )
    )
    assert labels, "search panel did not open (CodeMirror bundle missing search export?)"
    assert len(labels) == 3
    for lb in labels:
        assert lb["fontSize"] == "0px", f"toggle label native text must be hidden (font-size 0), got {lb['fontSize']}"
        assert lb["scrollWidth"] <= lb["boxWidth"] + 1, f"toggle label overflows its 24px box: {lb}"


def test_needs_attention_pane_stays_red_when_focused_and_yolo_ready(browser, tmp_path):
    # image 20260603-028: focusing/hovering a needs-attention (red) pane on a --dangerously-yolo server
    # made it `typing-ready-pane yolo-ready-pane needs-input-pane`; the yolo-ready green --panel-ring-color
    # (0,3,0) out-specified the needs red (0,2,0), so the alert went GREEN. The red must always win.
    css = app_css()
    combos = [
        "needs-input-pane",                                       # unfocused alert -> red (ring)
        "active-pane needs-input-pane",                           # focused alert -> red
        "typing-ready-pane yolo-ready-pane needs-input-pane",     # the bug: hovered + yolo + alert -> red
        "active-pane yolo-ready-pane needs-blocked-pane",
    ]
    panels = "".join(f'<div class="panel {c}" id="p{i}" style="width:160px;height:60px"></div>' for i, c in enumerate(combos))
    page = tmp_path / "needs-ring.html"
    page.write_text(f"<!doctype html><html><head><meta charset=utf-8><style>{css}</style></head>"
                    f'<body class="theme-dark">{panels}</body></html>', encoding="utf-8")
    browser.get(page.as_uri())
    rings = browser.execute_script(
        """
        const out = {};
        document.querySelectorAll('.panel').forEach(p => {
          out[p.id] = getComputedStyle(p).getPropertyValue('--panel-ring-color').trim();
        });
        return out;
        """
    )
    # Every needs-attention pane resolves the red ring color, regardless of focus/yolo-ready state.
    for pid, ring in rings.items():
        assert ring.lower() == '#ff3347', f"{pid}: needs-attention pane must keep the red ring (#ff3347), got {ring}"
