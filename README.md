# YOLOmux

Browser tools for watching, driving, and summarizing tmux sessions.

`yolomux.py` serves an interactive UI that attaches browser xterm.js terminals to local tmux sessions and adds agent-aware controls around them. Two companion tools ship alongside it: `auto_approve_tmux.py` (YOLO auto-approval without the UI) and `tmux_wall.py` (a read-only snapshot wall).

Developer / AI-agent docs (conventions, architecture, code layout, API reference) live in [`AGENTS.md`](AGENTS.md). This README covers installation and daily use.

## Quickstart

Run YOLOmux on the machine that owns the tmux sessions. It needs Python 3 and tmux. (Claude and Codex are optional — the `+ Claude` / `+ Codex` buttons only appear when those commands are on the server `PATH`.)

```bash
git clone https://github.com/keivenchang/yolomux.git
cd yolomux
tmux new-session -A -s project1     # start (or attach) a tmux session
python3 yolomux.py                  # serves on port 9998 by default
```

Then open `http://localhost:9998/`. On first launch you must enable a login account — see [Authentication & roles](#authentication--roles).

## Concepts

YOLOmux follows terminal-app terminology (iTerm2-style):

- **Pane** — a visible split region of the layout. The workspace has a left and right side; each side is one full-height pane or two stacked, for up to four panes. A pane shows one tab at a time and has its own toolbar.
- **Tab** — a thing shown inside a pane: a tmux session, or a virtual item (File Explorer, file editor, etc.). Tabs are numbered `1`–`9` by display order. A tab is **active** (shown in a pane), **minimized** (in a pane's tab stack but not shown), or **inactive** (not assigned to any pane).
- **Window** — a tmux window inside a tmux session. The pane toolbar's `<` / `>` controls step between a session's tmux windows.

## Daily use

Open YOLOmux, enable a login if the setup page asks, then refresh. Existing tmux sessions appear as tabs inside panes:

- Click a tab to show it in that pane.
- Use the `Tab` menu to activate minimized or inactive tabs.
- Drag a tab between pane tab bars, or onto a pane, to move or split the layout. Dropping in the middle of a pane moves the tab into that pane's tab bar; dropping near an edge splits the pane when there is room.
- The pane toolbar switches tmux windows (`<` / `>`), shows transcripts (`Tx`), asks for an AI summary (`AI`), opens the event log (`Log`), and collapses the info row (`Info`).
- The terminal border turns yellow for the pane that is focused and ready for typing.

The `YO` button toggles YOLO auto-approval for a tmux session. It watches the visible tmux screen for approval prompts and sends the approval key when the rule engine says the prompt is safe; the red `QUES?` / `EXEC?` badges come from that visible-screen detection, not transcript scraping. YOLO state is stored in `~/.config/yolomux/state.json`, so it survives page reloads and server restarts.

## UI features

- The menu bar contains `File`, `View`, `Tmux`, `Tab`, `Settings`, and `Help`. `File` opens the File Explorer / Finder and logout flow; `Tmux` creates and manages tmux sessions; `Tab` navigates active, minimized, and inactive tabs.
- The `Tab` menu groups tabs into **Active** (bright green, shown in panes), **Minimized** (in a pane's tab stack but not shown), and **Inactive** (not assigned to any pane).
- The `Tmux` menu shows a small count badge when YOLO is enabled for one or more sessions; its YOLO submenu opens/reloads the rule file and has a Sessions submenu for toggling each session.
- By default YOLOmux shows existing tmux sessions, capped at nine visible session tabs (`1`–`9`). It does not create default `yolomuxN` sessions.
- `+ Claude` / `+ Codex` create the next numbered tmux session with that agent (e.g. `7` when six exist). Each appears only when that CLI is on the server `PATH`; if neither is, YOLOmux shows `+ Term` and creates a plain shell session.
- Each session tab has its own `YO` button, status badges, session label, compact work description, and hide button.
- The layout is encoded in the page URL (`sessions`, `layout`, `tabs`), so a reload — or a bookmarked link — preserves the exact layout without browser storage.
- Mouse-wheel scrolling in a terminal sends tmux copy-mode scroll commands instead of scrolling the AI input area.
- Browser resize fits xterm immediately; the tmux resize is debounced until the resize settles.
- The pane window-control buttons (minimize / zoom / close) auto-detect your OS: macOS browsers get Mac traffic-light style, everything else (Windows, Linux) gets PC style. To force one, add a URL parameter: `?platform=pc` (also `win` / `windows` / `linux`) or `?platform=mac` (also `macos` / `darwin`) — for example `http://localhost:9998/?platform=pc`.
- `Settings` -> `Preferences` opens a draggable tab backed by `~/.config/yolomux/settings.yaml`. UI saves are atomic, running servers reload hand edits by polling the file, and open browsers poll `/api/settings` so changes made in another server instance apply without restart. Preferences also shows the active YOLO rules path and source.

## Files and editors

Open `File` -> `File Explorer` (`Finder` on macOS) to browse the server filesystem. The root path field is editable: press Enter to jump to a typed path, use Escape to revert, and use the copy button to copy the current root path. The `Root` / `Sync` toggle chooses whether the explorer stays on a fixed root or follows the focused tmux session's current directory.

Clicking a file opens it as a tab in the largest available pane, reusing an existing editor pane when one is already open. Text files can be edited and saved. The editor has `Edit`, `Preview`, and `Split` modes: Markdown renders as formatted HTML, code previews use the syntax-colored read view, and split mode keeps the editor and preview panes scrolled together. The wrap and line-number toggles are shared by editor tabs; wrapped continuation rows show a `↪` marker while source line numbers stay on real lines only. Markdown, shell, Python, JavaScript/TypeScript, Rust, JSON, HTML/XML/SVG, CSS, TOML, and YAML get lightweight syntax coloring. Files over the configured raw-read cap show a too-large state instead of loading into the editor.

Images open in the same tab system. Small images render at their original size; large images fit the available pane. Click the image to toggle between fit mode and original-size scroll mode.

Right-click a file or directory for file actions: copy the full path, copy the raw path, copy a repo-relative path when one exists, download files, rename, or delete. Shift-click selects a range; Ctrl/Cmd-click toggles individual rows. Dragging file rows into terminals sends the shell-quoted path text.

## Running it

Choose specific sessions:

```bash
python3 yolomux.py --sessions project1,project2
```

Run on a shared development host:

```bash
python3 yolomux.py --host 0.0.0.0 --port 9998
```

Run a background server with logs under `/tmp`:

```bash
setsid nohup env TERM=xterm-256color PYTHONUNBUFFERED=1 python3 yolomux.py --host 0.0.0.0 > /tmp/yolomux.log 2>&1 < /dev/null &
```

## HTTPS / TLS

HTTPS is off by default. To run with a generated self-signed certificate:

```bash
python3 yolomux.py --port 9998 --self-signed
```

Then open `https://localhost:9998/`. The browser warns because the certificate is self-signed. YOLOmux stores the generated PEM files under `~/.local/state/yolomux/tls/` and reuses them across restarts. To use your own certificate:

```bash
python3 yolomux.py --port 9998 --cert /path/fullchain.pem --key /path/privkey.pem
```

## Authentication & roles

**First launch.** YOLOmux creates `~/.config/yolomux/auth.yaml` as an inactive starter (directory `0700`, file `0600`; existing auth files are tightened on read, and an old default `user` / `password` file is replaced):

- The starter leaves `users:` uncommented but **comments out** every account: an admin entry for your login user (with a random generated password) and a readonly `guest` / `guest` entry. No login works until you uncomment one.
- Until an account is active, the server still listens but serves only a setup page (and prints a stdout warning) telling you to edit the file.
- YOLOmux re-reads `auth.yaml` on each setup poll, so the page reloads after you save — no restart needed.

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

**Sessions & cookies.** Once an account is active, browser navigation shows the login page and stores an HttpOnly cookie:

- The cookie has a 90-day sliding lifetime and survives server restarts — the signing secret lives in `~/.config/yolomux/auth-cookie-secret`. Changing a user's password or deleting that secret invalidates existing cookies.
- Cookies are scoped by port, so dev and production servers on the same host do not overwrite each other.
- HTTP Basic auth also works for clients that send an `Authorization` header.
- Served without HTTPS, YOLOmux warns and recommends restarting with `--self-signed`.

**Roles.**

| Role | Can do |
| --- | --- |
| `admin` | Type into tmux panes, create sessions, upload files, toggle `YO`, change Notify, switch tmux windows, run AI summaries. |
| `readonly` | View panes, transcripts, branch metadata, logs, and YOLO status. Terminals attach with `tmux attach-session -r`; mutating HTTP endpoints are rejected. |

## Agent permissions & YOLO

Agent launch flags are separate from YOLOmux's per-session `YO` toggle.

**Launching agents by hand.** Claude's auto permission mode still uses Claude's permission system but auto-handles more decisions:

```bash
claude --permission-mode auto
```

Claude's full bypass is `claude --dangerously-skip-permissions`. The current Codex CLI has no `--yolo` flag; the closest no-prompt setting removes approval prompts but keeps the sandbox policy:

```bash
codex --ask-for-approval never
```

Codex's full approval-and-sandbox bypass is `codex --dangerously-bypass-approvals-and-sandbox`.

**`--dangerously-yolo` (server flag).** Use it only when you intentionally want sessions that YOLOmux creates to launch with the dangerous bypass flags:

```bash
python3 yolomux.py --host 0.0.0.0 --port 9998 --dangerously-yolo
```

With it enabled, `+ Claude` / `+ Codex` create sessions with `claude --dangerously-skip-permissions` and `codex --dangerously-bypass-approvals-and-sandbox`. Without it, the same buttons create plain `claude` and `codex` sessions. The flag affects only new sessions created after the server starts; it does not change existing tmux sessions, and it is separate from the `YO` toggle.

**The `YO` toggle (runtime).** `YO` is per-session auto-approval for an *existing* tmux session. It watches the visible tmux screen and sends the approval key when the rule engine says the prompt is safe. It does not relaunch the agent and does not change the agent's own permission or sandbox flags.

**Rule file.** YOLOmux reads ordered rules from `~/.config/yolomux/yolo-rules.yaml` when that file exists. Rules are first-match-wins and support `command`, `regex`, `glob`, and `contains` matches with `approve`, `decline`, `block`, `ask`, `notify`, or `off` actions. The Tmux -> YOLO menu has `Open rule file` and `Reload rules`; Preferences shows the active path, source, rule count, and dry-run state. If the file is missing, YOLOmux uses a built-in fallback that preserves the previous dangerous-command block list while continuing to approve non-dangerous bash prompts. Invalid YAML is treated as `ask` and is surfaced in the UI and server stderr instead of silently allowing prompts.

Example:

```yaml
default: ask
rules:
  - name: block destructive
    type: command
    match: [rm, rmdir, shred, dd, mkfs]
    action: block
    risk: delete
  - name: safe reads
    type: regex
    match: '^(ls|cat|grep|git (status|log|diff))\b'
    action: approve
    risk: read
```

The YAML cannot relax the hard floor for `rm -rf /`, `dd` to block devices, fork bombs, `mkfs`, or redirection to block devices unless the server itself was started with `--dangerously-yolo`. Set `yolo.dry_run: true` in Preferences or `settings.yaml` to log what the rule engine would do without pressing an approval key.

## Remote access

The safer default is to keep YOLOmux bound to localhost on the server and tunnel it from your client:

```bash
autossh -M 0 -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 9998:127.0.0.1:9998 user@server
```

Then open `http://localhost:9998/` on the client.

If you bind with `--host 0.0.0.0`, the host firewall or cloud security group must allow the port — open TCP `9998` only from trusted client IPs. Do not expose the port broadly just because YOLOmux has authentication; the browser terminal can type into your tmux sessions.

```bash
sudo ufw allow from <client-ip> to any port 9998 proto tcp
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="<client-ip>" port protocol="tcp" port="9998" accept'
sudo firewall-cmd --reload
```

## xterm.js assets

YOLOmux serves xterm.js from a local editor install when available. It checks `YOLOMUX_XTERM_ROOTS` first, then `static/xterm`, then common Cursor, VS Code, and Windsurf server installs under the home directory. If `/static/xterm.js` or `/static/xterm.css` is missing, the browser falls back to jsDelivr.

## Companion: `auto_approve_tmux.py`

The standalone auto-approval tool — use it when you want YOLO behavior without the browser UI. It runs on the same server as tmux, polls the visible pane text with `tmux capture-pane`, detects Claude/Codex approval prompts, and sends the approval key with `tmux send-keys`.

```bash
python3 auto_approve_tmux.py --list                  # list tmux sessions
python3 auto_approve_tmux.py --dry-run --once project1   # preview one visible prompt
python3 auto_approve_tmux.py project1                 # watch one session
python3 auto_approve_tmux.py project1 project2        # watch several
python3 auto_approve_tmux.py project1,project2
python3 auto_approve_tmux.py "project*"               # glob
python3 auto_approve_tmux.py project1:0.1             # watch one window/pane
python3 auto_approve_tmux.py --verbose --dry-run project1   # debug detection
```

Run it in the background:

```bash
setsid nohup env PYTHONUNBUFFERED=1 python3 auto_approve_tmux.py --interval 0.5 "project*" > /tmp/auto_approve_tmux.log 2>&1 < /dev/null &
```

YOLOmux and this script share the same visible-screen detector. YOLOmux imports `auto_approve_tmux.py` as a module, wraps one `AutoApproveWorker` around each enabled session, and runs detected bash prompts through `yolomux_lib/yolo_rules.py` before sending tmux keys (flow: `JS YO button -> POST /api/auto-approve -> TmuxWebtermApp.set_auto_approve -> AutoApproveWorker -> auto_approve_tmux.py detector -> yolo_rules.py -> tmux send-keys`).

Detection intentionally uses the visible tmux screen for presence checks, which avoids approving stale prompts left in scrollback after the agent moved on. It recognizes active Codex working rows such as `• Working (4m 06s • esc to interrupt)`, and color rotation is not a blocker because `tmux capture-pane -p` returns text without terminal color styling. Dangerous shell commands are blocked by the default rules instead of approved.

## Companion: `tmux_wall.py` (read-only wall)

An optional read-only sidecar dashboard — useful when you only want a passive wall of terminal snapshots and JSON context, not the full interactive UI. It is a stdlib HTTP server that uses `tmux capture-pane` as the source, streams snapshots over Server-Sent Events, exposes JSON endpoints (for a future AI summarizer, without scraping the browser), optionally reads `container/show_project_containers.py` for container metadata, and serves `static/tmux-wall.css` / `static/tmux-wall.js`.

```bash
python3 tmux_wall.py --host 0.0.0.0 --port 8765      # then open http://localhost:8765/
python3 tmux_wall.py --print-targets                 # inspect target selection without starting
python3 tmux_wall.py --targets project1:0.0,project2:0.0,project3:1.0,project4:0.0 --slots 6
```

Without `--targets`, the server discovers panes from `project1` through `project4`, picks one agent pane per session first, then fills the remaining slots with other panes from those sessions. The wall API reference is in [`AGENTS.md`](AGENTS.md).

## License

YOLOmux is licensed under PolyForm Noncommercial 1.0.0. Noncommercial use is allowed under that license. Commercial use requires a separate commercial license from Keiven Chang.

Third-party code and generated dependency bundles keep their own upstream notices; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
