# YOLOmux

Browser tools for watching, driving, and summarizing tmux sessions.

## YOLOmux

`yolomux.py` serves the interactive YOLOmux UI. It attaches browser xterm.js terminals to local tmux sessions and adds agent-aware controls around them.

Run:

```bash
python3 yolomux.py
```

Then open:

```text
http://localhost:9998/
```

To expose it beyond localhost:

```bash
python3 yolomux.py --host 0.0.0.0 --port 9998
```

For a background development server, run it with `nohup`:

```bash
setsid nohup env TERM=xterm-256color PYTHONUNBUFFERED=1 python3 yolomux.py --host 0.0.0.0 > /tmp/yolomux.log 2>&1 < /dev/null &
```

On first launch, YOLOmux creates `~/.config/yolomux/auth.json` with placeholder credentials `user` / `password`. While those placeholders are active, the server still listens on the configured port, prints a large stdout setup warning, and serves only an auth setup page telling the user to edit that JSON file. Authentication is read only from this JSON file. YOLOmux reads the latest JSON auth on each request, so after saving `auth.json`, refresh the browser; no server restart is required.

YOLOmux serves xterm.js from a local editor install when available. It checks `YOLOMUX_XTERM_ROOTS` first, then `static/xterm`, then common Cursor, VS Code, and Windsurf server installs under the home directory. If `/static/xterm.js` or `/static/xterm.css` is missing, the browser falls back to jsDelivr.

## Webterm features

- The page title is `YOLOmux`.
- By default, YOLOmux shows the existing tmux sessions, capped at nine visible session tabs. Tabs are numbered by display order from `1` through `9`, so tab `1` is the first tmux session, tab `2` is the second, and so on.
- The `+ Claude` and `+ Codex` tabs create the next numbered tmux session with the selected agent, such as `7` when six sessions already exist. Each create tab appears only when that CLI is available on the YOLOmux server PATH. If neither CLI is available, YOLOmux shows `+ Term` and creates a plain shell session. YOLOmux does not create default `yolomuxN` sessions.
- The visible workspace has left and right sides. Each side can show one full-height pane or two stacked panes, for up to four visible panes total.
- Session panels are created once at page boot. Hidden sessions live in an off-screen panel pool instead of being destroyed, so drag/drop and quick switching do not restart unchanged terminals.
- The layout is stored in the page URL through readable `sessions`, `layout`, and `tabs` query parameters. Split positions are recorded as percentages in `layout`, so reloads preserve the layout without browser storage.
- YOLO state is stored server-side in `~/.config/yolomux/state.json`, so it survives page reloads and server restarts.
- The red mac-style circle hides a pane. The green circle expands or collapses a pane.
- Drag a top-tray session or a pane header into a visible slot. Dropping a pane in the middle of another pane swaps them. Dropping near the top or bottom stacks into that side when a slot is available.
- Each pane tab row has `YOLO`, previous/next tmux-window controls, `Terminal`, `Transcript`, `AI summary`, and right-aligned quick-switch buttons for replacing that pane with another session. Clicking the lit session hides that pane.
- The terminal border turns yellow only for the pane that is currently focused and ready for typing.
- Browser resize fits xterm immediately, but the tmux resize message is debounced so tmux is resized after the browser resize settles.
- Mouse wheel scrolling in a terminal sends tmux copy-mode scroll commands instead of scrolling the AI input area.

## How the webterm works

The server is a dependency-light Python `ThreadingHTTPServer`. It serves one HTML page, local xterm.js assets, JSON APIs, Server-Sent Events streams, and a WebSocket endpoint.

For each terminal connection, the browser opens `/ws?session=<tmux-session>`. The server creates a PTY, runs `tmux attach-session -t <tmux-session>` on the PTY slave, reads terminal bytes from the PTY master, and sends those bytes to xterm.js as WebSocket binary frames.

Browser input is sent as JSON messages over the same WebSocket. Normal keyboard data becomes `{"type": "input", "data": "..."}`. Resize messages become `{"type": "resize", "cols": ..., "rows": ...}`. Scroll messages become `{"type": "tmux-scroll", "direction": "up|down", "lines": ...}`.

Resizing is handled on the PTY slave file descriptor, then the server sends `SIGWINCH` to the tmux attach process group. The browser sends resize updates only after a debounce, except for an initial fast resize during WebSocket startup.

The browser creates panel DOM nodes for visible sessions at boot, checks that the backing tmux sessions exist, and starts terminal connections for them. Visible layout changes move existing panel nodes into slot containers. Hidden panels move back to `#panelPool`. This keeps xterm instances and WebSocket connections alive while changing layout.

Transcript metadata comes from tmux pane discovery plus local process-tree inspection. YOLOmux looks for Claude or Codex processes in the selected tmux pane, finds their transcript/session metadata, and exposes it in the pane header, transcript tab, and API responses.

The transcript tab uses Server-Sent Events from `/api/context-stream`. The AI summary tab uses `/api/summary-stream`, which builds a scoped prompt from the selected session's recent transcript and streams a Codex-generated summary back to the browser. Summary model settings can be overridden with `YOLOMUX_SUMMARY_MODEL`, `YOLOMUX_SUMMARY_EFFORT`, and `YOLOMUX_SUMMARY_SERVICE_TIER`.

YOLO uses `auto_approve_tmux.py` workers behind `/api/auto-approve`. The browser polls YOLO status every few seconds and reflects the active state in both the top tray and pane tab.

## Webterm API

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
