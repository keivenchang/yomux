# YOLOmux

Browser tools for watching, driving, and summarizing tmux sessions.

`yolomux.py` serves an interactive UI that attaches browser xterm.js terminals to local tmux sessions and adds agent-aware controls around them. Two companion tools ship alongside it: `auto_approve_tmux.py` (YOLO auto-approval without the UI) and `tmux_wall.py` (a read-only snapshot wall).

Developer / AI-agent docs (conventions, architecture, code layout, API reference) live in [`AGENTS.md`](AGENTS.md). Contributing and build instructions are in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).

## Development Checks

Before committing local changes, run the parallel check gate:

```bash
python3 tools/check.py
```

Use `python3 tools/check.py --list-lanes` to see focused lanes, `--lane <name>` for a smaller run, and `--serial` when debugging order or load-sensitive failures.

## Requirements

- Python 3.9+
- tmux
- `openssl` on `PATH` (only needed for `--self-signed` HTTPS)

## Quickstart

Recommended local run: HTTPS, login-gated, and YOLO-enabled for new Claude/Codex sessions created from the UI.

```bash
git clone https://github.com/keivenchang/yolomux.git
cd yolomux
pip install -r requirements.txt
tmux new-session -A -s project1     # start (or attach) a tmux session
python3 yolomux.py --self-signed --dang
```

Open `https://localhost:9998/`. The first launch shows a setup page — see [First launch](#first-launch) below. `--self-signed` creates a local HTTPS certificate under `~/.local/state/yolomux/tls/`; your browser will warn because it is not signed by a public CA. `--dang` is the short alias for `--dangerously-yolo`, which makes the UI's `+ Claude` and `+ Codex` buttons launch with their dangerous bypass flags.

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

When a Tab is a tmux session, that session has its own internal hierarchy — tmux windows (`Ctrl-b n/p`) and tmux panes (`Ctrl-b %/"`) — which belong to tmux, not YOLOmux. Watch the overloaded word **pane**: a YOLOmux Pane is a browser layout split, a tmux pane is a split inside a tmux window.

## Daily use

Open YOLOmux after setup. Existing tmux sessions appear as tabs. (The detailed pane/tab/Finder/Differ behavior contract lives in [`docs/GUI_SPECS.md`](docs/GUI_SPECS.md); this list is the daily-driver essentials.)

- Click a tab to show it in that pane. Use the `Tabs` menu to activate minimized or inactive tabs.
- Drag a tab between pane tab bars to move it, drop near a pane edge to split that pane, or drop on the outer root edge for a full-span pane. Pane splits are percentage-based and encode into the shareable page URL.
- Pinned tabs stay at the front of their pane and cannot be dragged into another pane.
- Drag a Finder or Differ file row into a pane to open that file there; dropping near a pane edge opens it in a new split.
- Re-opening an existing Finder/Differ file tab keeps it in the pane where you last moved it.
- Browser uploads from drag-drop, clipboard paste, or the `+` button default into a `.uploads/` subdirectory of the resolved target directory. The `uploads.subdir` Preference controls that folder; setting it blank restores the old direct-to-target behavior.
- Dropping or pasting a file onto a terminal can show a transient file-action menu with shortcut keys `1` through `n`, up to `9`. Agent panes get prompt actions such as OCR, summarize, review, or compare; plain shell panes get read-only shell commands such as `file`, `wc`, `tail -F`, `jq`, and `column -t`; server actions can show bounded previews, log scans, data stats, and OCR results. Upload Preferences control the suggestion menu, image paste action order, read-only shell autorun, and custom actions; image paste menus only show the configured image-order rows that apply to the current pane.
- Finder / File Explorer is docked by default on fresh and sessions-only URLs. Hiding it with `Mod+B`, the close button, or File -> Finder shows the restore shortcut in the status line.
- Finder / File Explorer self-heals after reconnect, wake, or Dockview remeasure if it vanished without an explicit user close. A deliberate hide stays hidden in that browser tab until restored.
- The pane info line shows one button per tmux window (`0:bash`, `1:codex`, ...); clicking a button switches that session to the matching tmux window. The pane toolbar shows transcripts (`Tx`), asks for an AI summary (`AI`), and opens the event log (`Log`).
- File -> `YO!share...` creates short live magic URLs for the entire current YOLOmux layout: panes, positions, active/background tabs, Finder/Differ/Tabber, editor/Preferences state, and every tmux session in those panes. Defaults are short-lived, read-only, http links for easy viewing; write access requires https. Viewers render the host viewport inside a scaled mirror frame, so layout, tabs, terminal output, scrolling, YO!info rows/sort/columns/scroll, Finder root/expansion, saved file/editor state, host menus, and the target-shaped ghost cursor track the host without resizing host tmux panes. The host can extend active shares and see connected users with duration, IP, and browser type; viewers see updated countdowns but cannot manage the share.
- Finder switches between the file tree and a full-pane `Differ` mode (per-repo FROM/TO diff controls). The root toggle defaults to `Sync`, which follows the focused session's affected repos and highlights changed paths.
- YO markers resync after client-events reconnect, page wake, or network restore, and fall back to a narrow auto-status poll while the live event stream is disconnected.
- Finder, Explorer, Differ, and Tabber path rows share the same file context menu. Image rows include `Copy image`, which writes image bytes to the browser clipboard when supported and otherwise falls back to copying the path.
- A third `Tabber` mode lists open tabs and each tmux session's windows (and the paths each agent touched), sorted by most recent activity — click a session to focus it, a window row to switch tmux windows.
- Finder browsing defaults to the filesystem root (`/`); credential-heavy paths such as `.ssh`, `.gnupg`, `.aws`, `.config/gh`, token files, and registry config files are blocked and hidden from search/index results.
- Quick Search (`Mod+P`) ranks filename matches ahead of path-only fuzzy matches; image hits render as `[Image #1] '/abs/path.png'` references.
- The language picker in the top bar, login/setup screens, and Preferences supports English, Traditional and Simplified Chinese, Japanese, Korean, Spanish, German, French, Italian, Brazilian Portuguese, Polish, Dutch, Hebrew, Arabic, Russian, Hindi, Vietnamese, Thai, and Turkish. The topbar picker stays open while background session refreshes run.
- The file editor's `Differ` toggle opens a diff view with a FROM/TO sha picker for any git-tracked file with commit history, even when the working tree is clean.
- Opening the same editable file through a symlink or hard-link path focuses the existing editor and keeps its dirty buffer instead of creating a second editor for the same physical file.
- Markdown Preview task checkboxes are editable for admin users: clicking a rendered `- [ ]` / `- [x]` item updates the underlying Markdown source through the normal dirty/autosave path.
- Sessions waiting at a detected permission prompt show an attention badge in the roster even when YOLO auto-approval is off, so pending `Yes/No` prompts do not silently sit idle.

### Copying terminal text

- Select text and press `Cmd-C` (Mac) / `Ctrl-C` (PC) to copy it to your browser clipboard. While a full-screen app like Claude owns the mouse, a normal drag goes to the app instead of making a selection — hold `Option` (Mac) / `Shift` (PC) and drag to force a real terminal selection, or just select inside the app: its own copy (sent as an OSC 52 escape) is forwarded to your browser clipboard automatically (the status line shows `copied N chars`).
- `Cmd-C` with nothing selected does nothing — it is never delivered to the running program. Plain `Ctrl-C` with nothing selected still sends `SIGINT` to interrupt the program.
- To copy the tmux copy-mode selection (server-side, via tmux), press `Cmd-Option-C` (Mac) / `Ctrl-Alt-C` (PC), or right-click and choose `Copy tmux selection`.
- Right-click keeps the current selection highlighted and offers `Copy` / `Copy without indent`.

The `YO` button toggles YOLO auto-approval for a tmux session. See [Agent permissions & YOLO](#agent-permissions--yolo).

## Running options

Specific sessions only:

```bash
python3 yolomux.py --sessions project1,project2 --self-signed --dang
```

Custom port (default is `9998`, host defaults to `0.0.0.0`):

```bash
python3 yolomux.py --port 8080 --self-signed --dang
```

Background server:

```bash
setsid nohup env TERM=xterm-256color PYTHONUNBUFFERED=1 python3 yolomux.py --self-signed --dang > /tmp/yolomux.log 2>&1 < /dev/null &
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
claude --bare                        # skip hooks and other customizations
codex --ask-for-approval never       # no approval prompts, sandbox still active
codex --dangerously-bypass-approvals-and-sandbox  # command approval and sandbox bypass
codex --dangerously-bypass-hook-trust             # hook trust bypass
```

`claude --dangerously-skip-permissions` bypasses Claude Code permission prompts. `claude --bare` is Claude Code's hook-side equivalent in version 2.1.177: it skips hooks, LSP, plugin sync, auto-memory, background prefetches, keychain reads, and `CLAUDE.md` auto-discovery. Claude Code does not expose a narrow hook-trust bypass flag equivalent to Codex's `--dangerously-bypass-hook-trust`.

`codex --dangerously-bypass-approvals-and-sandbox` lets Codex run model-generated commands without approval prompts and without the Codex command sandbox. `codex --dangerously-bypass-hook-trust` is separate: it allows enabled Codex hooks to run without persisted hook trust. It does not remove the normal command sandbox by itself.

**`--dang` / `--dangerously-yolo` (server flag).** Makes `+ Claude` / `+ Codex` buttons launch with the dangerous bypass flags:

```bash
python3 yolomux.py --self-signed --dang
```

With `--dang`, `+ Claude` launches `claude --dangerously-skip-permissions --bare`, so permission prompts are bypassed and hooks are skipped for new Claude sessions. `+ Codex` launches `codex --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust`, so both command approval/sandbox checks and hook trust checks are bypassed for new Codex sessions.

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

## License

YOLOmux is licensed under PolyForm Noncommercial 1.0.0. Noncommercial use is allowed under that license. Commercial use requires a separate commercial license from Keiven Chang.

Third-party code and generated dependency bundles keep their own upstream notices; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
