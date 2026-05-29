# YOLOmux

Browser tools for watching, driving, and summarizing tmux sessions.

Developer / AI agent docs (conventions, architecture, code layout, API reference) live in [`AGENTS.md`](AGENTS.md). This README covers installation and daily use.

## YOLOmux

`yolomux.py` serves the interactive YOLOmux UI. It attaches browser xterm.js terminals to local tmux sessions and adds agent-aware controls around them.

### Server setup

Run YOLOmux on the machine that owns the tmux sessions. The server needs Python 3 and tmux. Claude and Codex are optional, but the `+ Claude` and `+ Codex` buttons only appear when those commands are available on the server `PATH`.

Get the code onto that server:

```bash
git clone https://github.com/keivenchang/yolomux.git
cd yolomux
```

Start with an existing tmux session:

```bash
tmux new-session -A -s project1
```

Then launch YOLOmux:

```bash
python3 yolomux.py
```

Then open:

```text
http://localhost:9998/
```

To choose specific sessions:

```bash
python3 yolomux.py --sessions project1,project2
```

To run on a shared development host:

```bash
python3 yolomux.py --host 0.0.0.0 --port 9998
```

Optional HTTPS stays off by default. To run with a generated self-signed certificate:

```bash
python3 yolomux.py --port 9998 --self-signed
```

Then open `https://localhost:9998/`. The browser will show a certificate warning because the certificate is self-signed. YOLOmux stores the generated PEM files under `~/.local/state/yolomux/tls/` and reuses them across restarts. To use your own certificate:

```bash
python3 yolomux.py --port 9998 --cert /path/fullchain.pem --key /path/privkey.pem
```

For a background server, write logs under `/tmp`:

```bash
setsid nohup env TERM=xterm-256color PYTHONUNBUFFERED=1 python3 yolomux.py --host 0.0.0.0 > /tmp/yolomux.log 2>&1 < /dev/null &
```

Agent launch flags are separate from YOLOmux's per-session `YO` toggle. If you launch agents by hand, Claude's auto permission mode is:

```bash
claude --permission-mode auto
```

This is not the same as Claude's full permission bypass. It still uses Claude's permission system, but lets Claude auto-handle more decisions. Claude's full bypass is `claude --dangerously-skip-permissions`.

The current Codex CLI does not expose a `codex --yolo` flag. The closest no-prompt approval setting is:

```bash
codex --ask-for-approval never
```

That removes Codex approval prompts but keeps the configured sandbox policy. The full Codex approval-and-sandbox bypass is:

```bash
codex --dangerously-bypass-approvals-and-sandbox
```

Use YOLOmux `--dangerously-yolo` only when you intentionally want newly created Claude/Codex sessions to launch with their dangerous approval and sandbox bypass flags:

```bash
python3 yolomux.py --host 0.0.0.0 --port 9998 --dangerously-yolo
```

With that server flag enabled, the `+ Claude` and `+ Codex` buttons create new tmux sessions with these commands:

```bash
claude --dangerously-skip-permissions
codex --dangerously-bypass-approvals-and-sandbox
```

Without `--dangerously-yolo`, the same buttons create sessions with plain `claude` and `codex`. The flag affects only new sessions created by YOLOmux after the server starts. It does not change existing tmux sessions, and it is separate from the `YO` auto-approval toggle.

The `YO` button is runtime auto-approval for an existing tmux session. It watches the visible tmux screen for approval prompts and sends the approval key when the detector says the prompt is safe. It does not relaunch Claude or Codex, and it does not change the agent's own permission or sandbox flags.

On first launch, YOLOmux creates `~/.config/yolomux/auth.yaml` as an inactive starter file. The starter leaves `users:` uncommented, then comments out an admin account for the current login user with a random generated password plus a readonly `guest` / `guest` account. None of those credentials are active while their account entries are commented. YOLOmux writes the directory as `0700` and `auth.yaml` as `0600`; existing auth files are also tightened when read. If an old default `user` / `password` auth file is found, YOLOmux replaces it with the inactive starter. Until at least one account is uncommented, the server still listens on the configured port, prints a stdout setup warning, and serves only a setup page telling the user to edit that YAML file. YOLOmux reads the latest YAML auth on each setup poll, so after saving `auth.yaml`, the setup page reloads automatically; no server restart is required. Once users are active, browser navigation shows the YOLOmux login page and stores an HttpOnly cookie after sign-in. That cookie has a 90-day sliding lifetime and survives server restarts because YOLOmux stores the signing secret in `~/.config/yolomux/auth-cookie-secret`; changing the user's password or deleting that secret invalidates existing login cookies. Browser login cookies are scoped by YOLOmux port so dev and production servers on the same host do not overwrite each other. HTTP Basic auth still works for clients that explicitly send an `Authorization` header. If browser login is served without HTTPS, YOLOmux warns: highly recommend that you restart with `python3 yolomux.py --port 9998 --self-signed`.

Example `auth.yaml`:

```yaml
users:
  - username: "keivenc"
    password: "change-this-admin-password"
    role: "admin"
  - username: "guest"
    password: "guest"
    role: "readonly"
```

`admin` users can type into tmux panes, create sessions, upload files, toggle `YO`, change Notify, switch tmux windows, and run AI summaries. `readonly` users can view existing panes, transcripts, branch metadata, logs, and YOLO status. Readonly browser terminals attach to tmux with `tmux attach-session -r`, and mutating HTTP endpoints require admin access.

YOLOmux serves xterm.js from a local editor install when available. It checks `YOLOMUX_XTERM_ROOTS` first, then `static/xterm`, then common Cursor, VS Code, and Windsurf server installs under the home directory. If `/static/xterm.js` or `/static/xterm.css` is missing, the browser falls back to jsDelivr.

### Remote access

The safer default is to keep YOLOmux bound to localhost on the server and tunnel it from your client:

```bash
autossh -M 0 -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 9998:127.0.0.1:9998 user@server
```

Then open this on the client:

```text
http://localhost:9998/
```

If you bind with `--host 0.0.0.0`, the host firewall or cloud security group must allow the selected port. For example, open TCP `9998` only from trusted client IPs. Do not expose the port broadly just because YOLOmux has basic authentication; the browser terminal can type into your tmux sessions.

Firewall examples:

```bash
sudo ufw allow from <client-ip> to any port 9998 proto tcp
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="<client-ip>" port protocol="tcp" port="9998" accept'
sudo firewall-cmd --reload
```

### Daily use

Open YOLOmux, edit `~/.config/yolomux/auth.yaml` if the setup page asks for credentials, then refresh. Existing tmux sessions appear as tabs inside panes. Click a tab to show it in that pane. A tab that is inside a pane but not currently shown is a background tab. A tab that is not assigned to any pane is inactive. Use the `Panes` menu to activate background or inactive tabs. Drag tabs between pane tab bars or onto a pane to split the layout. Use the pane toolbar to switch tmux windows, show transcripts, ask for an AI summary, inspect the event log, or collapse the info row.

The `YO` button toggles YOLO auto-approval for that tmux session. YOLO state is stored in `~/.config/yolomux/state.json`, so it survives page reloads and server restarts. The red `QUES?` and `EXEC?` badges come from visible tmux screen detection, not transcript scraping.

## auto_approve_tmux.py

`auto_approve_tmux.py` is the standalone auto-approval tool. Use it when you want YOLO behavior without the browser UI. It runs on the same server as tmux, polls the visible pane text with `tmux capture-pane`, detects Claude/Codex approval prompts, and sends the selected approval key with `tmux send-keys`.

List available tmux sessions:

```bash
python3 auto_approve_tmux.py --list
```

Dry-run one visible prompt before enabling it:

```bash
python3 auto_approve_tmux.py --dry-run --once project1
```

Watch one session:

```bash
python3 auto_approve_tmux.py project1
```

Watch several sessions:

```bash
python3 auto_approve_tmux.py project1 project2
python3 auto_approve_tmux.py project1,project2
python3 auto_approve_tmux.py "project*"
```

Watch one tmux window or pane:

```bash
python3 auto_approve_tmux.py project1:0.1
```

Run it in the background:

```bash
setsid nohup env PYTHONUNBUFFERED=1 python3 auto_approve_tmux.py --interval 0.5 "project*" > /tmp/auto_approve_tmux.log 2>&1 < /dev/null &
```

Use `--verbose` when debugging prompt detection:

```bash
python3 auto_approve_tmux.py --verbose --dry-run project1
```

The standalone script and YOLOmux use the same detector. YOLOmux imports `auto_approve_tmux.py` as a Python module and wraps one `AutoApproveWorker` around each enabled session. The GUI endpoint flow is `JS YO button -> POST /api/auto-approve -> TmuxWebtermApp.set_auto_approve -> AutoApproveWorker -> auto_approve_tmux.py -> tmux capture-pane/send-keys`.

Prompt detection intentionally uses the visible tmux screen for presence checks. That avoids approving stale prompts that remain in scrollback after the agent has moved on. The script also recognizes active Codex working rows such as `• Working (4m 06s • esc to interrupt)`, and color rotation is not a blocker because `tmux capture-pane -p` returns the text without terminal color styling. Dangerous shell commands are blocked instead of approved.

## Webterm features

- The page title is `YOLOmux`.
- Terminology follows terminal apps such as iTerm2: a pane is a visible split region, and a tab is a tmux session or virtual item shown inside a pane.
- By default, YOLOmux shows the existing tmux sessions, capped at nine visible session tabs. Tabs are numbered by display order from `1` through `9`, so tab `1` is the first tmux session, tab `2` is the second, and so on.
- The `Panes` menu groups tabs into Active, Background tabs, and Inactive. Active tabs are the bright green tabs currently shown in panes. Background tabs are in a pane's tab stack but not currently shown. Inactive tabs are not assigned to any pane.
- The `+ Claude` and `+ Codex` tabs create the next numbered tmux session with the selected agent, such as `7` when six sessions already exist. Each create tab appears only when that CLI is available on the YOLOmux server PATH. If neither CLI is available, YOLOmux shows `+ Term` and creates a plain shell session. YOLOmux does not create default `yolomuxN` sessions.
- The visible workspace has left and right sides. Each side can show one full-height pane or two stacked panes, for up to four visible panes total.
- Session panels are created once at page boot. Hidden sessions live in an off-screen panel pool instead of being destroyed, so drag/drop and quick switching do not restart unchanged terminals.
- The layout is stored in the page URL through readable `sessions`, `layout`, and `tabs` query parameters. Split positions are recorded as percentages in `layout`, so reloads preserve the layout without browser storage.
- YOLO state is stored server-side in `~/.config/yolomux/state.json`, so it survives page reloads and server restarts.
- Drag a tab or pane header into a visible slot. Dropping a tab in the middle of another pane moves it into that pane's tab bar. Dropping near the top, bottom, left, or right splits the target pane when there is enough room.
- Each tmux-session tab has its own `YO` button, status badges, session label, compact work description, and `X` hide button. Hidden tabs appear under the `Panes` menu as background or inactive tabs.
- Each pane toolbar has previous/next tmux-window controls, a terminal button labeled from the active tmux window process such as `bash`, `codex`, or `mock/mock_codex.py`, plus `Tx`, `AI`, `Log`, and `Info`.
- The terminal border turns yellow only for the pane that is currently focused and ready for typing.
- Browser resize fits xterm immediately, but the tmux resize message is debounced so tmux is resized after the browser resize settles.
- Mouse wheel scrolling in a terminal sends tmux copy-mode scroll commands instead of scrolling the AI input area.

## Read-only wall

`tmux_wall.py` is an optional read-only sidecar dashboard. It is useful when you only need a passive wall of terminal snapshots and JSON context, not the full interactive YOLOmux terminal UI.

- Stdlib HTTP server.
- Server-Sent Events for live terminal snapshots.
- `tmux capture-pane` as the terminal source.
- Existing `container/show_project_containers.py` as optional container metadata.
- JSON endpoints that can feed a future AI summarizer without scraping the browser.
- Static frontend assets in `static/tmux-wall.css` and `static/tmux-wall.js`.

Run:

```bash
python3 tmux_wall.py --host 0.0.0.0 --port 8765
```

Then open:

```text
http://localhost:8765/
```

Without `--targets`, the server discovers panes from `project1` through `project4`, picks one agent pane per session first, then fills the remaining six slots with other panes from those sessions.

Current target selection can be inspected without starting the server:

```bash
python3 tmux_wall.py --print-targets
```

To override:

```bash
python3 tmux_wall.py --targets project1:0.0,project2:0.0,project3:1.0,project4:0.0 --slots 6
```

Wall API reference is in [`AGENTS.md`](AGENTS.md).

## License

YOLOmux is licensed under PolyForm Noncommercial 1.0.0. Noncommercial use is allowed under that license. Commercial use requires a separate commercial license from Keiven Chang.

Third-party code and generated dependency bundles keep their own upstream notices; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
