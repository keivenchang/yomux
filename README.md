# YOLOmux

Browser tools for watching, driving, and summarizing tmux sessions.

`yolomux.py` serves an interactive UI that attaches browser xterm.js terminals to local tmux sessions and adds agent-aware controls around them. Two companion tools ship alongside it: `auto_approve_tmux.py` (YOLO auto-approval without the UI) and `tmux_wall.py` (a read-only snapshot wall).

Developer / AI-agent docs (conventions, architecture, code layout, API reference) live in [`AGENTS.md`](AGENTS.md). Contributing and build instructions are in [`DEVELOPMENT.md`](DEVELOPMENT.md).

## Requirements

- Python 3.9+
- tmux
- `openssl` on `PATH` (only needed for `--self-signed` HTTPS)

## Quickstart

```bash
git clone https://github.com/keivenchang/yolomux.git
cd yolomux
pip install -r requirements.txt
tmux new-session -A -s project1     # start (or attach) a tmux session
python3 yolomux.py                  # serves on http://0.0.0.0:9998
```

Open `http://localhost:9998/`. The first launch shows a setup page — see [First launch](#first-launch) below.

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

- **Pane** — a visible split region of the layout. The workspace has a left and right side; each side is one full-height pane or two stacked, for up to four panes. A pane holds one or more tabs but shows ONE at a time.
- **Tab** — the thing shown inside a pane. Tab types: **tmux session** (terminal), **Finder / File Explorer** (file browser with `Differ` mode), **File** (text editor or image viewer), **Preferences**, and **YO!agent**.

When a Tab is a tmux session, that session has its own internal hierarchy — tmux windows (`Ctrl-b n/p`) and tmux panes (`Ctrl-b %/"`) — which belong to tmux, not YOLOmux. Watch the overloaded word **pane**: a YOLOmux Pane is a browser layout split, a tmux pane is a split inside a tmux window.

## Daily use

Open YOLOmux after setup. Existing tmux sessions appear as tabs:

- Click a tab to show it in that pane.
- Use the `Tabs` menu to activate minimized or inactive tabs.
- Drag a tab between pane tab bars to move it, or drop near a pane edge to split the layout.
- The pane toolbar steps through the focused session's tmux windows (`<` / `>`), shows transcripts (`Tx`), asks for an AI summary (`AI`), and opens the event log (`Log`).
- In dark mode, the focused pane's tab strip brightens so the active pane is easier to pick out at a glance; light mode keeps the same pane strip color.
- Inactive panes use a flat dim from the visually active pane, and that visual-active pane survives terminal blur.
- Finder can switch between the file tree and a full-pane `Differ` mode; diff mode uses the Differ chrome with session selection, per-repo FROM/TO controls, and no create-file actions.
- The Finder root toggle is `No Sync` / `Sync`, and defaults to `Sync`. In Sync mode, Finder follows the focused session's affected repos: if a session touches multiple nearby repos, Finder opens their common parent, expands each touched repo path, and bolds affected repo roots plus changed descendant directories; with no changed-file payload yet, or with a stale same-session payload from a different repo, it falls back to the session's git root, and a fresh session with no cwd opens home.
- Finder browsing defaults to the filesystem root (`/`) instead of only home and `/tmp`; credential-heavy paths such as `.ssh`, `.gnupg`, `.aws`, `.config/gh`, token files, and registry config files are blocked and hidden from search/index results.
- Finder file and diff rows share the same file date modes (`None`, `Date`, `Ago`); in Finder, the date toggle and Reload control sit together on the toolbar's right edge so row times line up under the date column.
- Finder diff rows keep status, diff counts, and the time column visible by trimming long filenames first.
- Editor diff rows use one consistent red or green fill for changed lines, including the cursor's active line.
- The file editor's `Differ` toggle opens a diff view with a FROM/TO sha picker for any git-tracked file with commit history, including a file with no uncommitted changes. The default HEAD-vs-working diff for a clean file is empty, but diff mode stays open so you can pick two refs to compare; it only falls back to the editor for files git cannot diff.
- Diff panes hold the loading state until the requested FROM/TO payload is ready, then render the diff without flashing a transient edit view.
- The terminal border turns yellow for the focused pane.
- Sessions waiting at a detected permission prompt show an attention badge in the roster even when YOLO auto-approval is off, so pending `Yes/No` prompts do not silently sit idle.

The `YO` button toggles YOLO auto-approval for a tmux session. See [Agent permissions & YOLO](#agent-permissions--yolo).

## Running options

Specific sessions only:

```bash
python3 yolomux.py --sessions project1,project2
```

Custom port (default is `9998`, host defaults to `0.0.0.0`):

```bash
python3 yolomux.py --port 8080
```

Background server:

```bash
setsid nohup env TERM=xterm-256color PYTHONUNBUFFERED=1 python3 yolomux.py > /tmp/yolomux.log 2>&1 < /dev/null &
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
| `admin` | Type into tmux panes, create sessions, upload files, toggle `YO`, switch tmux windows, run AI summaries. |
| `readonly` | View panes, transcripts, branch metadata, logs, and YOLO status. Terminals are read-only. |

Cookies have a 90-day sliding lifetime and survive server restarts. Cookies are scoped by port, so dev and production servers on the same host do not overwrite each other. Changing a user's password invalidates existing cookies for that user.

## Agent permissions & YOLO

**Launching agents.** Claude's auto permission mode:

```bash
claude --permission-mode auto        # auto-handles most decisions
claude --dangerously-skip-permissions  # full bypass
codex --ask-for-approval never       # no approval prompts, sandbox still active
codex --dangerously-bypass-approvals-and-sandbox  # full bypass
```

**`--dangerously-yolo` (server flag).** Makes `+ Claude` / `+ Codex` buttons launch with the full bypass flags:

```bash
python3 yolomux.py --dangerously-yolo
```

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
python3 yolomux.py --host 127.0.0.1 --port 9998
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

## License

YOLOmux is licensed under PolyForm Noncommercial 1.0.0. Noncommercial use is allowed under that license. Commercial use requires a separate commercial license from Keiven Chang.

Third-party code and generated dependency bundles keep their own upstream notices; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
