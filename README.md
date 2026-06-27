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

Recommended local run: HTTPS, login-gated, all tmux sessions visible, and YOLO-enabled for new Claude/Codex sessions created from the UI.

```bash
git clone https://github.com/keivenchang/yolomux.git
cd yolomux
pip install -r requirements.txt
tmux new-session -A -s project1     # optional: create one if you do not already have tmux sessions
python3 yolomux.py --self-signed --dang
```

Open `https://localhost:9998/`. The first launch shows a setup page — see [First launch](#first-launch) below. With no `--sessions` filter, YOLOmux discovers every tmux session from `tmux list-sessions`. `--self-signed` creates a local HTTPS certificate under `~/.local/state/yolomux/tls/`; your browser will warn because it is not signed by a public CA. `--dang` is the short alias for `--dangerously-yolo`, which makes the UI's `+ Claude` and `+ Codex` buttons launch with their dangerous bypass flags.

For local automated verification only, `YOLOMUX_TEST_AUTH_BYPASS=1` starts the server with a test admin identity so scripts and Selenium can call login-gated routes without creating cookies. Do not use that environment variable for normal runs, production, or any server reachable by untrusted clients.

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
- Hover a tmux tab to see each Claude/Codex tmux sub-window in that session near the top of the popover, with working agents first, idle durations from recent activity, and path/branch/git metadata under the matching AI tmux sub-window when sub-windows differ.
- Drag a tab between pane tab bars to move it, drop near a pane edge to split that pane, or drop on the outer root edge for a full-span pane. Pane splits are percentage-based and encode into the shareable page URL.
- Pinned tabs stay at the front of their pane and cannot be dragged into another pane.
- Drag a Finder or Differ file row into a pane to open that file there; dropping near a pane edge opens it in a new split.
- Re-opening an existing Finder/Differ file tab keeps it in the pane where you last moved it.
- Browser uploads from drag-drop, clipboard paste, or the `+` button default into a `.uploads/` subdirectory of the resolved target directory. The `uploads.subdir` Preference controls that folder; setting it blank restores the old direct-to-target behavior.
- Dropping or pasting a file onto a terminal can show a transient file-action menu with shortcut keys `1` through `n`, up to `9`. Agent panes get prompt actions such as OCR, summarize, review, or compare; plain shell panes get read-only shell commands such as `file`, `wc`, `tail -F`, `jq`, and `column -t`; server actions can show bounded previews, log scans, data stats, and OCR results. Upload Preferences control the suggestion menu, image paste action order, read-only shell autorun, and custom actions; image paste menus only show the configured image-order rows that apply to the current pane.
- Terminal panes measure against the bundled mono font and ignore duplicate resize-observer echoes once the pane size and cell metrics are unchanged, so a tmux grid fills the pane instead of flickering between transient widths.
- The mouse wheel scrolls full-screen apps (Claude, Codex, vim, less) at the same speed as a normal scrollback pane. Those panes own the mouse and keep no tmux scrollback, so the wheel is forwarded to the app instead of into tmux copy-mode; a normal shell pane still scrolls its tmux scrollback.
- Finder / File Explorer is docked by default on fresh and sessions-only URLs. Hiding it with `Mod+B`, the close button, or File -> Finder shows the restore shortcut in the status line.
- Finder / File Explorer self-heals after reconnect, wake, or Dockview remeasure if it vanished without an explicit user close. A deliberate hide stays hidden in that browser tab until restored.
- The pane Info Bar shows one button per tmux sub-window (`0:bash`, `1:codex`, ...); clicking a button switches that session to the matching tmux sub-window, and the path/repo metadata follows that selected sub-window instead of inheriting touched repos from another sub-window in the same session. Each Claude/Codex tmux sub-window has its own path/branch/git state; a plain shell tmux sub-window has cwd/path but no agent state. The pane toolbar shows transcripts (`Tx`), asks for an AI summary (`AI`), and opens the event log (`Log`).
- File -> `Search & Runs` opens a data pane that searches captured session events and summaries, then lists compact run history rows with prompt, cwd, agent, timing, final state, PR, and latest summary.
- File -> `YO!info` opens the grouped relationship tree over Tab, AI, path, branch, PR, and Linear metadata. It renders one record per Tab x AI x Path x Branch association, keeps unassigned branch-inventory rows visible, preserves collapsed groups across refreshes, and lets the grouping order switch between presets such as `Tab > Path`, `Path > Branch`, `Linear > PR`, and `PR > Branch` or custom per-level selectors.
- File -> `YO!stats` opens debug performance stats as a normal tab that can live in any pane; it enables in-page collection without reloading or adding `debug=1` to the URL. `API/SSE` shows recent request/event text and counters; `Graph` shows line-series history split by unit: API/SSE count per second, latency, bandwidth per second, and CPU each get their own Y axis with concrete unit labels and X-axis time ticks. The CPU chart overlays `yolomux.py` CPU and system average CPU on a fixed 0-100% scale. The graph has 1, 5, and 10 second scale buttons, selectable ranges from last minute through 24 hours, plus a meta row with `yolomux.py uptime`, PID, RSS, server sequence, and total upload/download MB. The 8 hour and 16 hour range buttons appear only after retained history reaches those ages. Graph history is remembered in the server for browser refreshes, fetched incrementally by sequence, and bounded to 24 hours with data older than one hour rolled into ten-second buckets. `python3 yolomux.py --print-runtime-report` prints a no-listener JSON report for owner/coalescing state, cache sizes, top endpoints, event types, and largest transcripts.
- A session shows its real repo and branch even when the agent was launched from your home directory or another non-repo path: YOLOmux infers the repo from the directories the agent has actually edited (read from its transcript), not just the pane's current directory. A pane sitting in a directory with no git checkout and no edits shows `no git checkout detected`.
- File -> `YO!share...` creates short live magic URLs for the current YOLOmux layout. Defaults are short-lived, read-only, http links; write access requires https. The host can extend active shares and see connected users with duration, IP, and browser type. Replay details live in [`docs/specs/SHARE_MIRRORING.md`](docs/specs/SHARE_MIRRORING.md).
- Finder switches between the file tree and a full-pane `Differ` mode (per-repo FROM/TO diff controls). The root toggle defaults to `Sync`, which follows the last clicked/typed session's affected repos and highlights changed paths; stale background syncs from other sessions cannot retarget it afterward.
- File editor refresh errors preserve the backend/API reason and HTTP status before falling back to a generic `Cannot inspect <path>` message; valid files in the current worktree continue refreshing without being marked unknown.
- YO markers resync after client-events reconnect, page wake, or network restore, and fall back to a narrow auto-status poll while the live event stream is disconnected.
- Finder, Explorer, Differ, and Tabber path rows share the same file context menu. Image rows include `Copy image`, which writes image bytes to the browser clipboard when supported and otherwise falls back to copying the path.
- A third `Tabber` mode lists open tabs and each tmux session's sub-windows, sorted by recent activity. Click any non-arrow part of a green session row such as `2 #10731...` to focus that tmux tab, click the disclosure arrow to collapse/expand it, and click a sub-window row to switch tmux sub-windows. Agent touched-path rows are cached by the server, attach only under the matching agent tmux sub-window, and keep their recency text visible while the tree text truncates first.
- YO!agent keeps its chat transcript across server restarts until you click `Clear conversation`. It is the central command surface for product-state and Preferences questions, safe admin-approved settings changes, session watches, finish notifications, robust sends to detected Claude/Codex panes, wait-then-send jobs, and multi-agent handoffs. Explicit target-session sends show target results back in the YO!agent chat by default unless you ask it not to wait. It preserves perspective: `ask agent 1 to <do ...>` sends only `<do ...>` to agent `1`, and handoffs pass exact, summarized, or modified information only when that is the requested form. More examples and transport rules live in [`docs/YOAGENT_SKILLS.md`](docs/YOAGENT_SKILLS.md) and [`docs/specs/YOAGENT_COMMON_INTENTS_AND_AGENT_COMMUNICATION.md`](docs/specs/YOAGENT_COMMON_INTENTS_AND_AGENT_COMMUNICATION.md).
- YO!agent-managed Claude/Codex SDK, app-server, MCP, and exec sessions are background job/conversation targets unless YOLOmux also starts a real visible TUI for them. They show up through YO!agent jobs, waits, Recent Agents, and result messages with their provider, thread id, cwd, model, sandbox/approval settings, and result source; YOLOmux does not fake them as normal tmux tabs.
- SDK-backed managed transports are optional. Install `requirements-yoagent-managed.txt` in the Python environment that runs YOLOmux to enable `claude-sdk` and `codex-sdk`; without those packages YO!agent reports a structured missing-SDK diagnostic and can still use CLI/app-server/MCP transports where available.
- YO!agent jobs persist in YOLOmux state across server restarts. Notify jobs fire browser/toast notifications through the client-events stream; wait-then-send jobs revalidate the exact target agent prompt state before sending. High-risk prompt text such as secrets, recursive deletes, broad resets, broad `pkill`, recursive permission changes, or SSH commands requires confirmation instead of auto-sending.
- YO!skills are loaded from built-in YOLOmux skill files first, then user-local files in `~/.config/yolomux/skills.d/`; user context Markdown lives in `~/.config/yolomux/context.d/`. Built-ins bootstrap common workflows and stay read-only. User-local files can add, override, disable, update, or delete skills. See [`docs/YOAGENT_SKILLS.md`](docs/YOAGENT_SKILLS.md) for schema, examples, and management commands.
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

`YOLOMUX_TEST_AUTH_BYPASS=1` is the only no-login mode, and it is for local tests and one-off verification commands. When enabled, YOLOmux treats non-share requests as admin, while share-token requests remain scoped readonly guests.

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

**Do not use `claude --bare` with YOLOmux.** `--bare` is a minimal mode (skips hooks, LSP, plugin sync, auto-memory, background prefetches, keychain reads, and `CLAUDE.md` auto-discovery), but it also makes Claude Code read Anthropic auth **strictly** from `ANTHROPIC_API_KEY` or `apiKeyHelper` — OAuth and the keychain are never read. With a subscription or enterprise OAuth login (the common YOLOmux setup) and no API key in the environment, a `--bare` session has no usable credential and shows "Not logged in · Please run /login". YOLOmux therefore launches Claude without `--bare`. Claude Code does not expose a narrow hook-trust bypass flag equivalent to Codex's `--dangerously-bypass-hook-trust`.

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

Never combine `YOLOMUX_TEST_AUTH_BYPASS=1` with a remotely reachable bind address. The bypass is only for localhost/dev test harnesses.

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
