# YOLOmux

Browser tools for watching, driving, and summarizing tmux sessions.

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
tmux new-session -A -s dynamo1
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
python3 yolomux.py --sessions dynamo1,dynamo2
```

To run on a shared development host:

```bash
python3 yolomux.py --host 0.0.0.0 --port 9998
```

For a background server, write logs under `/tmp`:

```bash
setsid nohup env TERM=xterm-256color PYTHONUNBUFFERED=1 python3 yolomux.py --host 0.0.0.0 > /tmp/yolomux.log 2>&1 < /dev/null &
```

Use `--dangerously-yolo` only when you intentionally want newly created Claude/Codex sessions to launch with their dangerous approval and sandbox bypass flags:

```bash
python3 yolomux.py --host 0.0.0.0 --port 9998 --dangerously-yolo
```

With that server flag enabled, the `+ Claude` and `+ Codex` buttons create new tmux sessions with these commands:

```bash
claude --dangerously-skip-permissions
codex --dangerously-bypass-approvals-and-sandbox
```

Without `--dangerously-yolo`, the same buttons create sessions with plain `claude` and `codex`. The flag affects only new sessions created by YOLOmux after the server starts. It does not change existing tmux sessions, and it is separate from the `YO` auto-approval toggle.

On first launch, YOLOmux creates `~/.config/yolomux/auth.yaml` with placeholder credentials `user` / `password`. While those placeholders are active, the server still listens on the configured port, prints a large stdout setup warning, and serves only an auth setup page telling the user to edit that YAML file. YOLOmux reads the latest YAML auth on each request, so after saving `auth.yaml`, refresh the browser; no server restart is required. If an old `~/.config/yolomux/auth.json` exists and no YAML file exists yet, YOLOmux migrates the single JSON user into `auth.yaml`.

Example `auth.yaml`:

```yaml
users:
  - username: "admin"
    password: "change-this-admin-password"
    role: "admin"
  - username: "viewer"
    password: "change-this-viewer-password"
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

Open YOLOmux, edit `~/.config/yolomux/auth.yaml` if the setup page asks for credentials, then refresh. Existing tmux sessions appear as tabs inside window tab bars. Click a tab to focus that pane. Click `X` on a tab to minimize it into the top strip. Click the top-strip button to restore it into a window. Drag tabs between window tab bars or onto a pane to split the layout. Use the pane toolbar to switch tmux windows, show transcripts, ask for an AI summary, inspect the event log, or collapse the info row.

The `YO` button toggles YOLO auto-approval for that tmux session. YOLO state is stored in `~/.config/yolomux/state.json`, so it survives page reloads and server restarts. The red `QUES?` and `EXEC?` badges come from visible tmux screen detection, not transcript scraping.

## auto_approve_tmux.py

`auto_approve_tmux.py` is the standalone auto-approval tool. Use it when you want YOLO behavior without the browser UI. It runs on the same server as tmux, polls the visible pane text with `tmux capture-pane`, detects Claude/Codex approval prompts, and sends the selected approval key with `tmux send-keys`.

List available tmux sessions:

```bash
python3 auto_approve_tmux.py --list
```

Dry-run one visible prompt before enabling it:

```bash
python3 auto_approve_tmux.py --dry-run --once dynamo1
```

Watch one session:

```bash
python3 auto_approve_tmux.py dynamo1
```

Watch several sessions:

```bash
python3 auto_approve_tmux.py dynamo1 dynamo2
python3 auto_approve_tmux.py dynamo1,dynamo2
python3 auto_approve_tmux.py "dynamo*"
```

Watch one tmux window or pane:

```bash
python3 auto_approve_tmux.py dynamo1:0.1
```

Run it in the background:

```bash
setsid nohup env PYTHONUNBUFFERED=1 python3 auto_approve_tmux.py --interval 0.5 "dynamo*" > /tmp/auto_approve_tmux.log 2>&1 < /dev/null &
```

Use `--verbose` when debugging prompt detection:

```bash
python3 auto_approve_tmux.py --verbose --dry-run dynamo1
```

The standalone script and YOLOmux use the same detector. YOLOmux imports `auto_approve_tmux.py` as a Python module and wraps one `AutoApproveWorker` around each enabled session. The GUI endpoint flow is `JS YO button -> POST /api/auto-approve -> TmuxWebtermApp.set_auto_approve -> AutoApproveWorker -> auto_approve_tmux.py -> tmux capture-pane/send-keys`.

Prompt detection intentionally uses the visible tmux screen for presence checks. That avoids approving stale prompts that remain in scrollback after the agent has moved on. The script also recognizes active Codex working rows such as `• Working (4m 06s • esc to interrupt)`, and color rotation is not a blocker because `tmux capture-pane -p` returns the text without terminal color styling. Dangerous shell commands are blocked instead of approved.

## Webterm features

- The page title is `YOLOmux`.
- By default, YOLOmux shows the existing tmux sessions, capped at nine visible session tabs. Tabs are numbered by display order from `1` through `9`, so tab `1` is the first tmux session, tab `2` is the second, and so on.
- The `+ Claude` and `+ Codex` tabs create the next numbered tmux session with the selected agent, such as `7` when six sessions already exist. Each create tab appears only when that CLI is available on the YOLOmux server PATH. If neither CLI is available, YOLOmux shows `+ Term` and creates a plain shell session. YOLOmux does not create default `yolomuxN` sessions.
- The visible workspace has left and right sides. Each side can show one full-height pane or two stacked panes, for up to four visible panes total.
- Session panels are created once at page boot. Hidden sessions live in an off-screen panel pool instead of being destroyed, so drag/drop and quick switching do not restart unchanged terminals.
- The layout is stored in the page URL through readable `sessions`, `layout`, and `tabs` query parameters. Split positions are recorded as percentages in `layout`, so reloads preserve the layout without browser storage.
- YOLO state is stored server-side in `~/.config/yolomux/state.json`, so it survives page reloads and server restarts.
- Drag a pane tab or pane header into a visible slot. Dropping a pane in the middle of another pane moves it into that window tab bar. Dropping near the top, bottom, left, or right splits the target pane when there is enough room.
- Each pane tab has its own `YO` button, status badges, session label, compact work description, and `X` minimize button. Minimized sessions appear in the top strip as compact buttons.
- Each pane toolbar has previous/next tmux-window controls, a terminal button labeled from the active tmux window process such as `bash`, `codex`, or `mock_codex.py`, plus `Tx`, `AI`, `Log`, and `Info`.
- The terminal border turns yellow only for the pane that is currently focused and ready for typing.
- Browser resize fits xterm immediately, but the tmux resize message is debounced so tmux is resized after the browser resize settles.
- Mouse wheel scrolling in a terminal sends tmux copy-mode scroll commands instead of scrolling the AI input area.

## How the webterm works

The server is a dependency-light Python `ThreadingHTTPServer`. It serves one HTML page, local xterm.js assets, JSON APIs, Server-Sent Events streams, and a WebSocket endpoint.

For each terminal connection, the browser opens `/ws?session=<tmux-session>`. The server creates a PTY, runs `tmux attach-session -t <tmux-session>` for admin users or `tmux attach-session -r -t <tmux-session>` for readonly users on the PTY slave, reads terminal bytes from the PTY master, and sends those bytes to xterm.js as WebSocket binary frames.

Browser input is sent as JSON messages over the same WebSocket. Normal keyboard data becomes `{"type": "input", "data": "..."}`. Resize messages become `{"type": "resize", "cols": ..., "rows": ...}`. Scroll messages become `{"type": "tmux-scroll", "direction": "up|down", "lines": ...}`.

Resizing is handled on the PTY slave file descriptor, then the server sends `SIGWINCH` to the tmux attach process group. The browser sends resize updates only after a debounce, except for an initial fast resize during WebSocket startup.

The browser creates panel DOM nodes for visible sessions at boot, checks that the backing tmux sessions exist, and starts terminal connections for them. Visible layout changes move existing panel nodes into slot containers. Hidden panels move back to `#panelPool`. This keeps xterm instances and WebSocket connections alive while changing layout.

Transcript metadata comes from tmux pane discovery plus local process-tree inspection. YOLOmux looks for Claude or Codex processes in the selected tmux pane, finds their transcript/session metadata, and exposes it in the pane header, transcript tab, and API responses.

The transcript tab uses Server-Sent Events from `/api/context-stream`. The AI summary tab uses `/api/summary-stream`, which builds a scoped prompt from the selected session's recent transcript and streams a Codex-generated summary back to the browser. Summary model settings can be overridden with `YOLOMUX_SUMMARY_MODEL`, `YOLOMUX_SUMMARY_EFFORT`, and `YOLOMUX_SUMMARY_SERVICE_TIER`.

YOLO uses `auto_approve_tmux.py` workers behind `/api/auto-approve`. The browser polls YOLO status every few seconds and reflects the active state in each pane tab.

## Webterm API

All API routes require auth. Read endpoints accept `readonly` or `admin`, except `/api/summary-stream` because it launches Codex and requires `admin`. Mutating POST routes require `admin` except `/api/event`, which accepts readonly client telemetry. `/ws` accepts readonly users but attaches tmux with `-r` and ignores keyboard input and tmux-scroll messages.

- `GET /api/transcripts` returns pane, process, and transcript-path metadata.
- `GET /api/tmux?session=dynamo1&lines=90` returns a tmux capture-pane snapshot.
- `GET /api/transcript?session=dynamo1&lines=120` returns the transcript tail for one session.
- `GET /api/context?session=dynamo1&messages=40` returns a compact, message-oriented transcript tail.
- `GET /api/context-items?session=dynamo1&messages=40` returns structured transcript items.
- `GET /api/context-stream?session=dynamo1&messages=200` streams structured transcript items with Server-Sent Events.
- `GET /api/summary-stream?session=dynamo1&lookback=3600` streams a Codex-generated summary with Server-Sent Events.
- `GET /api/auto-approve` returns YOLO status for all sessions.
- `GET /api/auto-approve?session=dynamo1` returns YOLO status for one session.
- `POST /api/create-session?agent=claude`, `POST /api/create-session?agent=codex`, or `POST /api/create-session?agent=term` creates the next numbered tmux session with the selected agent, capped at nine visible sessions. Claude and Codex requests are rejected if the selected CLI is not available on the YOLOmux server PATH; `term` is available only as the fallback when neither CLI is available.
- `POST /api/ensure-session?session=dynamo1` checks that a tmux session still exists.
- `POST /api/auto-approve?session=dynamo1&enabled=1` enables or disables YOLO for a session.
- `POST /api/tmux-next?session=dynamo1` moves the session to the next tmux window.
- `GET /ws?session=dynamo1` attaches a browser terminal to tmux.

Inspect the transcript mapping without starting the server:

```bash
python3 yolomux.py --print-transcripts
```

## Read-only wall

`tmux_wall.py` is a read-only dashboard:

- Stdlib HTTP server.
- Server-Sent Events for live terminal snapshots.
- `tmux capture-pane` as the terminal source.
- Existing `container/show_dynamo_containers.py` as optional container metadata.
- JSON endpoints that can feed a future AI summarizer without scraping the browser.

Run:

```bash
python3 tmux_wall.py --host 0.0.0.0 --port 8765
```

Then open:

```text
http://localhost:8765/
```

Without `--targets`, the server discovers panes from `dynamo1` through `dynamo4`, picks one agent pane per session first, then fills the remaining six slots with other panes from those sessions.

Current target selection can be inspected without starting the server:

```bash
python3 tmux_wall.py --print-targets
```

To override:

```bash
python3 tmux_wall.py --targets dynamo1:0.0,dynamo2:0.0,dynamo3:1.0,dynamo4:0.0 --slots 6
```

## Wall API

- `GET /api/snapshot` returns the current six-pane dashboard payload.
- `GET /api/transcript?target=dynamo1:0.0&lines=2000` returns one tmux pane transcript.
- `GET /api/summary-input?lines=1200` returns the active dashboard panes and container metadata as one JSON payload.

These wall endpoints do not call an LLM. They are the stable input surface for a later summarizer.

## License

YOLOmux is licensed under PolyForm Noncommercial 1.0.0. Noncommercial use is allowed under that license. Commercial use requires a separate commercial license from Keiven Chang.

Third-party code and generated dependency bundles keep their own upstream notices; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
