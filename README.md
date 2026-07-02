# YOLOmux

Browser tools for watching, driving, and summarizing tmux sessions.

`yolomux.py` serves an interactive UI that attaches browser xterm.js terminals to local tmux sessions and adds agent-aware controls around them. Two companion tools ship alongside it: `auto_approve_tmux.py` (YOLO auto-approval without the UI) and `tmux_wall.py` (a read-only snapshot wall).

Contributor and build instructions live in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md). AI-agent conventions live in [`AGENTS.md`](AGENTS.md), and detailed product behavior lives under [`docs/specs/`](docs/specs/).

## Requirements

- Python 3.9+
- tmux
- `openssl` on `PATH` (only needed for `--self-signed` HTTPS)

## Quickstart

Recommended local run: HTTPS, login-gated, all tmux sessions visible, and YOLO-enabled for new Claude/Codex sessions created from the UI.

```bash
git clone https://github.com/keivenchang/yolomux.git
cd yolomux
make setup          # pip install -e ".[yoagent]" + xterm.js assets + build the bundle  (run `make help` for more)
tmux new-session -A -s project1     # optional: create one if you do not already have tmux sessions
python3 yolomux.py --self-signed --dang   # or: make run
```

On an externally managed system Python (PEP 668), create and activate a virtualenv first (`python3 -m venv .venv && . .venv/bin/activate`), then `make setup`. Not using `make`? `pip install -e ".[yoagent]"` installs the deps + the `yolomux` command (pip enforces Python 3.9+), then `npm install` for local xterm.js assets.

Open `https://localhost:9998/`. The first launch shows a setup page — see [First launch](#first-launch) below. With no `--sessions` filter, YOLOmux discovers every tmux session from `tmux list-sessions`. `--self-signed` creates a local HTTPS certificate under `~/.local/state/yolomux/tls/`; your browser will warn because it is not signed by a public CA. `--dang` is the short alias for `--dangerously-yolo`, which makes the UI's `+ Claude` and `+ Codex` buttons launch with their dangerous bypass flags.

## First launch

On first run YOLOmux creates `~/.config/yolomux/auth.yaml` with every account commented out. No login works until you uncomment one:

```bash
# edit the file — nano, vim, whatever you prefer
nano ~/.config/yolomux/auth.yaml
```

Uncomment the admin entry (it uses your login username and a random generated password):

```yaml
users:
  - username: "yourname"
    password: "generated-password-shown-in-file"
    role: "admin"
```

Save the file. The setup page polls and reloads automatically — no server restart needed. Then log in.

To add a read-only guest account, uncomment (or add) a `readonly` entry:

```yaml
  - username: "guest"
    password: "guest"
    role: "readonly"
```

## Concepts

YOLOmux follows terminal-app terminology (iTerm2-style):

- **Pane** — a visible split region of the layout. Panes tile via draggable splits and can be nested arbitrarily (drag a tab to a pane edge to split, or to its header to add a tab). A pane holds one or more tabs but shows ONE at a time.
- **Tab** — the thing shown inside a pane. Tab types: **tmux session** (terminal), **Finder / File Explorer** (file browser with `Differ` mode), **File** (text editor or image viewer), **Preferences**, and **YO!agent**.

When a Tab is a tmux session, that session has its own internal hierarchy — tmux sub-windows (`Ctrl-b n/p`) and tmux panes (`Ctrl-b %/"`) — which belong to tmux, not YOLOmux. Watch the overloaded word **pane**: a YOLOmux Pane is a browser layout split, a tmux pane is a split inside a tmux sub-window.

## Daily use

Open YOLOmux after setup. Existing tmux sessions appear as tabs. (The detailed pane/tab/Finder/Differ behavior contract lives in [`docs/specs/GUI.md`](docs/specs/GUI.md); this list is the daily-driver essentials.)

- Click a tab to show it in that pane. Use the `Tabs` menu to activate minimized or inactive tabs.
- Press `?` for Keyboard Shortcuts and Legends, including the green play, yellow pause, and red stop status glyph meanings.
- Drag a tab between pane tab bars to move it, drop near a pane edge to split that pane, or drop on the outer root edge for a full-span pane. Pane splits are percentage-based and encode into the shareable page URL.
- Drag a Finder or Differ file row into a pane to open that file there; dropping near a pane edge opens it in a new split.
- Upload or paste files with drag-drop, clipboard paste, or the `+` button. Dropping a file on a terminal offers actions suited to an AI or shell pane.
- Use the pane Info Bar to switch tmux sub-windows (`0:bash`, `1:codex`, ...), open transcripts (`Tx`), request an AI summary (`AI`), or inspect the event log (`Log`).
- File -> `Search & Runs` opens a data pane that searches captured session events and summaries, then lists compact run history rows with prompt, cwd, agent, timing, final state, PR, and latest summary.
- File -> `YO!info` opens a grouped relationship tree over tabs, agents, paths, branches, pull requests, and Linear work.
- File -> `YO!stats` opens API/SSE events and performance graphs for client traffic, CPU, agent status, and agent tokens. Use its scale and time-range controls to inspect recent or retained history; the detailed behavior contract lives in [`docs/specs/GUI.md`](docs/specs/GUI.md).
- The pane header pop-out button opens supported file previews, YO!info, and YO!stats in a detached browser window.
- File -> `YO!share...` creates short live magic URLs for the current YOLOmux layout. Defaults are short-lived, read-only, http links; write access requires https. The host can extend active shares and see connected users with duration, IP, and browser type. Replay details live in [`docs/specs/SHARE_MIRRORING.md`](docs/specs/SHARE_MIRRORING.md).
- Finder includes file browsing, editing, preview, search, and per-repository `Differ` views. Quick Search is `Mod+P`.
- Tabber lists open tabs and tmux sub-windows by recent activity, and the top-bar language picker changes the live UI language.
- YO!agent handles product questions, session watches, notifications, safe sends, wait-then-send jobs, and multi-agent handoffs. See [`docs/YOAGENT_SKILLS.md`](docs/YOAGENT_SKILLS.md) for setup and examples.
- Tab attention badges surface agents waiting for input or approval even when automatic approval is off.

For exact UI behavior, edge cases, and coverage, see [`docs/specs/GUI.md`](docs/specs/GUI.md).

### Copying terminal text

- Select text and press `Cmd-C` (Mac) / `Ctrl-C` (PC) to copy it to your browser clipboard. While a full-screen app like Claude owns the mouse, a normal drag goes to the app instead of making a selection — hold `Option` (Mac) / `Shift` (PC) and drag to force a real terminal selection, or just select inside the app: its own copy (sent as an OSC 52 escape) is forwarded to your browser clipboard automatically (the status line shows `copied N chars`).
- `Cmd-C` with nothing selected does nothing — it is never delivered to the running program. Plain `Ctrl-C` with nothing selected still sends `SIGINT` to interrupt the program.
- To copy the tmux copy-mode selection (server-side, via tmux), press `Cmd-Option-C` (Mac) / `Ctrl-Alt-C` (PC), or right-click and choose `Copy tmux selection`.
- Right-click keeps the current selection highlighted and offers `Copy` / `Copy without indent`. When Claude owns the visible highlighted block and sends it through OSC 52, the right-click menu must preserve that app-side block; it must not re-read and copy only the small text under the cursor.
- Right-clicking a URL in a terminal pane or rendered markdown puts `Open URL in a new tab` first, then `Copy URL`; when the visible selected text differs from the actual href, the menu labels that path explicitly as `Copy selected text`.
- After a terminal copy/open action consumes selected text, YOLOmux clears stale browser/xterm selection. Explicit `Copy tmux selection` also exits tmux copy-mode after copying so selected rows do not stay painted as green blocks.

The `YO` button toggles YOLO auto-approval for a tmux session. See [Agent permissions & YOLO](#agent-permissions--yolo).

## Running options

All tmux sessions, default behavior:

```bash
python3 yolomux.py --self-signed --dang
```

Custom port (default is `9998`, host defaults to `0.0.0.0`):

```bash
python3 yolomux.py --port 8080 --self-signed --dang
```

Background server:

```bash
setsid nohup env TERM=xterm-256color PYTHONUNBUFFERED=1 python3 yolomux.py --self-signed --dang > /tmp/yolomux.log 2>&1 < /dev/null &
```

Specific tmux sessions only, optional filter:

```bash
python3 yolomux.py --sessions project1,project2 --self-signed --dang
```

## HTTPS / TLS

```bash
python3 yolomux.py --self-signed          # auto-generated cert, stored in ~/.local/state/yolomux/tls/
python3 yolomux.py --cert fullchain.pem --key privkey.pem   # bring your own
```

`--self-signed` requires `openssl` on `PATH`. Browsers warn because the certificate is self-signed; proceed past the warning.

## Authentication & roles

| Role | Can do |
| --- | --- |
| `admin` | Type into tmux panes, create sessions, upload files, toggle `YO`, switch tmux sub-windows, run AI summaries. |
| `readonly` | View panes, transcripts, branch metadata, logs, and YOLO status. Terminals are read-only. |

Cookies have a 90-day sliding lifetime and survive server restarts. Cookies are scoped by port, so dev and production servers on the same host do not overwrite each other. Changing a user's password invalidates existing cookies for that user.

## Agent permissions & YOLO

**Launching agents.** Claude's auto permission mode:

```bash
claude --permission-mode auto        # auto-handles most decisions
claude --dangerously-skip-permissions  # full bypass
codex --ask-for-approval never       # no approval prompts, sandbox still active
codex --dangerously-bypass-approvals-and-sandbox  # command approval and sandbox bypass
codex --dangerously-bypass-hook-trust             # hook trust bypass
```

`claude --dangerously-skip-permissions` bypasses Claude Code permission prompts.

`codex --dangerously-bypass-approvals-and-sandbox` lets Codex run model-generated commands without approval prompts and without the Codex command sandbox. `codex --dangerously-bypass-hook-trust` is separate: it allows enabled Codex hooks to run without persisted hook trust. It does not remove the normal command sandbox by itself.

**`--dang` / `--dangerously-yolo` (server flag).** Makes `+ Claude` / `+ Codex` buttons launch with the dangerous bypass flags:

```bash
python3 yolomux.py --self-signed --dang
```

With `--dang`, `+ Claude` launches `claude --dangerously-skip-permissions`, so permission prompts are bypassed for new Claude sessions (hooks and OAuth login are left intact — see the note above on why `--bare` is not used). `+ Codex` launches `codex --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust`, so both command approval/sandbox checks and hook trust checks are bypassed for new Codex sessions.

Without it, those buttons create plain `claude` / `codex` sessions. This flag does not change existing sessions.

**The `YO` toggle.** Per-session auto-approval for an existing tmux session. It watches the visible tmux screen and sends the approval key when the rule engine says the prompt is safe. Rules live in `~/.config/yolomux/yolo-rules.yaml`:

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

The `tmux` menu has `Open rule file` and `Reload rules`. Set `yolo.dry_run: true` in Preferences to log what the rule engine would do without pressing a key.

The optional `risk:` field is a label shown in the YOLO event log. Keep it to the boring concrete set so the audit display stays consistent: `read`, `edit`, `network`, `process`, `delete`, `credential`, `unknown`. Any other string is accepted (the engine never rejects a rule for its risk label), it just won't be standardized.

## Remote access

YOLOmux binds `--host 0.0.0.0` (all interfaces) by default, on purpose: the product is built for reaching your sessions from a phone or another machine on a trusted LAN, and every request is gated by the login layer. If that's your setup, restrict the port to trusted IPs at the firewall:

```bash
sudo ufw allow from <client-ip> to any port 9998 proto tcp
```

To keep YOLOmux local-only instead, bind loopback and tunnel from your client:

```bash
python3 yolomux.py --host 127.0.0.1 --port 9998 --self-signed --dang
autossh -M 0 -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 9998:127.0.0.1:9998 user@server
```

## Companion: `auto_approve_tmux.py`

Standalone YOLO auto-approval without the browser UI:

```bash
python3 auto_approve_tmux.py --list                       # list tmux sessions
python3 auto_approve_tmux.py --dry-run --once project1    # preview one visible prompt
python3 auto_approve_tmux.py project1                     # watch one session
python3 auto_approve_tmux.py "project*"                   # glob
```

Background:

```bash
setsid nohup env PYTHONUNBUFFERED=1 python3 auto_approve_tmux.py --interval 0.5 "project*" > /tmp/auto_approve.log 2>&1 < /dev/null &
```

## Companion: `tmux_wall.py`

Read-only snapshot wall — passive view of terminal panes with no login layer (refuses non-loopback by default):

```bash
python3 tmux_wall.py --port 8765
python3 tmux_wall.py --targets project1:0.0,project2:0.0 --slots 4
```

Set `YOLOMUX_CONTAINER_HELPER=/path/to/show_project_containers.py` if the wall should include container metadata from a helper outside `~/utils/container/show_project_containers.py`.

## License

YOLOmux is licensed under PolyForm Noncommercial 1.0.0. Noncommercial use is allowed under that license. Commercial use requires a separate commercial license from Keiven Chang.

Third-party code and generated dependency bundles keep their own upstream notices; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
