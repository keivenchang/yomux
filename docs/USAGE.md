# Using YOmux

The full guide: requirements, first launch, daily use, sharing, accounts, agent permissions, and remote access. For a short introduction, see the [README](../README.md).

## Requirements

- tmux and Git on the server. Install the Claude or Codex CLI to run those agents and YO!agent.
- To build: Bazel 9.2.0 and a C compiler. Bazel downloads Go 1.27.1 itself; you do not need Go installed.
- Node.js and npm, only when setup must rebuild the editor bundles because no verified cache exists.
- Python 3.11 or newer, only for developer tools and tests. Installing and running YOmux does not need Python.
- `openssl`, only for the optional local certificate authority helper `tools/setup-tls.sh`. The default self-signed certificate is generated in Go.
- On Linux, raise the inotify limits to `fs.inotify.max_user_instances=1024`, `fs.inotify.max_user_watches=1048576`, and `fs.inotify.max_queued_events=65536`. YOmux still starts with lower limits, but warns in the server log and browser and shows a command to raise them. See [Linux inotify limits](DEVELOPMENT.md#linux-inotify-limits) to make them permanent.

### What `make setup` does

`make setup` builds the native server with Bazel, downloads checksum-pinned browser libraries, fonts, and PDF.js, builds the editors, and installs the `yomux` and `yolomux` commands below `~/.local` (change it with `PREFIX=...`). After setup, YOmux starts without network access, Node.js, or Python. The install records its source checkout in `install-source.json`; keep that checkout, because self-update builds from it.

## First launch

On first run YOmux creates `~/.config/yomux/auth.yaml` (mode `0600`) with one admin account commented out. It uses your login name and a random password. No one can log in until you uncomment it:

```bash
nano ~/.config/yomux/auth.yaml
```

```yaml
users:
  - username: "yourname"
    password: "generated-password-shown-in-file"
    role: "admin"
```

Save the file. The setup page reloads by itself; no restart is needed. Then log in.

To add a read-only account, add another entry:

```yaml
  - username: "guest"
    password: "guest"
    role: "readonly"
```

YOmux keeps an existing `auth.yaml` that has content. An empty file is refilled with the starter content. Symlinks and other non-regular files are rejected.

### Launch options

Always pass a port. `./boot.sh 9998` runs this checkout; `yomux --port 9998` runs the installed copy. The installed commands also read `YOMUX_PORT` or `YOMUX_DEFAULT_PORT`. If the port is in use, YOmux refuses to start unless you add `--force`, and even then it replaces only a verified YOmux instance.

| Option | Effect |
| --- | --- |
| `--host ADDRESS` | Bind address. `boot.sh` and `yomux` default to `0.0.0.0` (all interfaces); the `yomux-go` binary run directly defaults to `127.0.0.1`. |
| `--http` | Serve plain HTTP instead of HTTPS. |
| `--cert FILE --key FILE` | Use your own certificate. Otherwise YOmux creates and reuses a self-signed one under `${TMPDIR:-/tmp}/yomux-go-tls-<port>/`. |
| `--force` | Replace a running YOmux instance on the same port. |

Environment variables use the `YOMUX_` prefix.

YOmux always uses the launching user's default tmux server, the same one `tmux list-sessions` shows. Sessions on separately named servers (`tmux -L`, `tmux -S`) or owned by other Unix users do not appear.

## Concepts

- **Pane**: a region of the browser window that holds tabs and shows one at a time. Generic Panes tile with draggable splits. Side Panes are narrow left or right panes for Finder, Differ, and Tabber.
- **Tab**: what a pane shows: a tmux session (terminal), Finder (files), Differ (changed files), Tabber (recent tabs and windows), Repo Diff (Git history), a file (editor, preview, diff, or image), Preferences, YO!agent, YO!chat, or YO!stats.

A terminal tab shows a tmux session, which has its own windows (`Ctrl-b n`/`p`) and panes (`Ctrl-b %`/`"`). Those belong to tmux, not YOmux. Note the two meanings of "pane": a YOmux pane is a browser split; a tmux pane is a split inside a tmux window.

YOmux always targets tmux sessions by exact name (`=<session>:`), so an action can never hit a similarly named session.

## Daily use

Press `?` for the keyboard shortcuts and the meaning of each status icon. The detailed interaction rules are in the [UI contract](specs/GUI.md).

### Tabber, panes, and tabs

- Tabber lists your agent conversations, terminals, files, and tools, newest activity first. Each row shows the agent's status, the tmux name, the Git branch, and a short description. Pinned rows stay at the top. While the pointer is over Tabber, rows keep their order so they do not jump under your cursor; the new order applies when the pointer leaves.
- Normal mode shows one content pane: select a row in Tabber to show it, select it again to hide it. Advanced mode adds free splits and drag-and-drop. Switch modes in General Preferences or with the checkbox next to Tabber's minimize control.
- Right-click (or long-press on touch) a tab for actions such as split, expand, pin, rename, and kill. Drag tabs between panes, or onto a pane edge to split it. The layout is saved in the page URL.
- New Chat starts a new agent with the next free numeric tmux name. Right-click a row to rename its description or its tmux name.
- Automatic descriptions: every 10 minutes, YOmux asks an AI model to summarize each conversation that changed and was active in the last 30 minutes. The default is Codex `gpt-6-luna` at low effort; choosing a `claude-` model uses Claude instead. If that client is not installed and available, nothing is renamed. Generated descriptions start with `[AI summary] `, are at most 96 characters, and never replace a description you wrote. Change these settings in General Preferences.
- `Mod+P` opens Quick Open; `Mod+B` hides or restores Finder, Differ, and Tabber.

### Terminals

- Status icons and badges show which agents are working, finished, or waiting for input or approval. The browser title and favicon count working agents, one per tmux window.
- Select text and press `Cmd-C` (Mac) or `Ctrl-C` (PC) to copy. When an app such as Claude owns the mouse, hold `Option` (Mac) or `Shift` (PC) while dragging to select terminal text, or copy inside the app; YOmux forwards the app's copy (OSC 52) to your clipboard.
- `Cmd-C` with nothing selected does nothing. `Ctrl-C` with nothing selected interrupts the program, as usual.
- `Cmd-Option-C` (Mac) or `Ctrl-Alt-C` (PC), or right-click and Copy tmux selection, copies the tmux copy-mode selection.
- Right-click a URL to open or copy it. Right-click a file path to open it in an editor.
- In Codex, the mouse wheel scrolls the conversation. Hold Shift while scrolling, or use Shift+Page Up/Down, to scroll tmux history; Esc leaves tmux copy mode.
- Drop or paste a file onto a terminal to upload it; YOmux offers actions that suit an agent or a shell. Terminal uploads are private to your login and stored under `${TMPDIR:-/tmp}/yolomux-server-<uid>/shared/yolomux.<login-user>/uploads/<session>/`.

### Finder, Differ, and Repo Diff

- Finder opens any absolute path the server process can read. It marks uncommitted changes (staged, unstaged, deleted, renamed, and untracked) on every file and folder, and stars the paths the current session touched.
- Finder follows the selected session: it opens that session's repository and remembers your expanded folders.
- Right-click a file or folder and choose Get Info for size, dates, and permissions. Admins can change permissions there.
- Differ lists changed files and which agent changed them. You can compare any two Git refs.
- Right-click a Git folder and choose Show Diff for Repo Diff: paged commit history; expand a commit to see its message and files, and select a file for its diff.
- Drag a file from Finder or Differ into a pane to open it there.

### Editors and previews

- Text files open in TextEdit (CodeMirror). Markdown and HTML also have ViewEdit, which lets you edit the rendered page directly. Split shows TextEdit and ViewEdit side by side and keeps them in sync about 1.25 seconds after you stop typing; saving, switching modes, or closing syncs immediately.
- Paste an image into a Markdown file to save it next to the document: for `/a/b/file.md`, the image goes into `/a/b/file/` and the editor inserts `![upload](file/upload.png)`.
- Click Δ in the editor toolbar to review saved changes since you started watching, or over the last 1, 5, 15, or 30 minutes (Markdown and text files up to 1 MiB). When the file changes on disk, YOmux merges the change into your draft, or pauses autosave and asks you if the changes overlap.
- Previews:
  - PDF, images, Mermaid diagrams, and Graphviz (`.dot`, `.gv`) with zoom and pan.
  - SQLite (`.sqlite`, `.sqlite3`, `.db`): read-only tables, schema, and 50-row pages, from files up to 20 MiB.
  - LaTeX (`.tex`, `.ltx`): compiles the current buffer to PDF without saving, and in Split keeps source and PDF scrolled together. YOmux uses Tectonic or pdfLaTeX if installed, or offers to download a checksum-verified Tectonic. Compilation runs sandboxed (bubblewrap on Linux, the built-in sandbox on macOS) with shell escape off.
  - Word and PowerPoint (`.docx`, `.pptx`): converted to PDF with LibreOffice on Linux, sandboxed with bubblewrap. Files up to 25 MiB, 30-second limit, two conversions at a time. Install with `sudo apt install libreoffice bubblewrap util-linux`. Office preview is not available on macOS yet.

### Agents, chat, and notifications

- YO!agent (File -> YO!agent) holds conversations, runs skills, and watches sessions. It can send a prompt to a session, but always asks you first. See [YO!agent](YOAGENT.md).
- YO!chat (File -> YO!chat) is a shared chat for the users of one YOmux server, with history, search, and typing indicators. Start a message with `/yo` to ask YO!agent.
- Notifications from other panes appear in one toast rail and never take your focus. Click a notification to go to its tab. Preferences control in-app and system notifications.

### YO!stats

File -> YO!stats shows Graphs, Cost, API/SSE, Daemons, and Logs: host CPU, memory, and disk, agent processes, and token usage and cost.

## Share a workspace

YO!Share invites someone into chosen tabs of your workspace with a link. Open File -> YO!Share. Expand New Share, choose the tabs, name the guest, set the duration and the number of browsers, then select Create New Share and Copy URL. The invitation link is copied to your clipboard.

- Files are shared read-only by default. Turn on Writable to let the guest edit a file, or type into a terminal. Terminal input asks for confirmation the first time, because the guest can run any command as the account that owns the terminal.
- Writable text and Markdown files are edited together: everyone sees each other's carets, in TextEdit and in Markdown ViewEdit. Save your own draft before making a file writable.
- When you share a Markdown file, the images it references in its folder are shared with it. For LaTeX, guests see the PDF you publish.
- Guests get a workspace with Tabber, the editors, a countdown, and a chat. Guest access ends when the share expires, when you select Stop Sharing, or when the server restarts.
- You can change tabs, write access, expiry, and the browser limit of an active share at any time.

## Accounts and roles

| Role | Can do |
| --- | --- |
| `admin` | Everything: type into terminals, create and kill sessions, read and write files, upload, share, toggle YO, and configure YO!agent. |
| `readonly` | View terminals, transcripts, branch information, logs, YO status, Repo Diff history and statistics, and Finder listings and filename search. Cannot read file contents, see file diffs or commit patches, write, upload, or type into terminals. |

- Login cookies last 90 days. Changing a password signs out that account's existing cookies.
- Browser writes, logins, and terminal connections from another scheme, host, or port are rejected. Command-line requests without browser origin headers still work.
- Repeated failed logins are throttled. Limits can be tuned in `login-rate-limit.json` in the config directory; see [Backend architecture](specs/BACKEND_ARCHITECTURE.md#credentials).

## Agent permissions and YO

### New sessions

File -> New offers each agent with normal permissions or with dangerous auto-approve. Dangerous launches run:

```bash
claude --dangerously-skip-permissions
codex --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust
```

You choose per session; there is no server-wide setting.

### The YO toggle

Turn on `YO` for an existing session to answer its permission prompts with your rules. Rules live in `~/.config/yomux/yolo-rules.yaml`:

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

- `type` is `command` or `regex`. `action` is `approve`, `decline`, `block`, `ask`, `notify`, or `off`.
- `risk` is an optional label: `read`, `edit`, `network`, `process`, `delete`, `credential`, or `unknown`.
- The tmux menu has Open rule file and Reload rules. Set `yolo.dry_run: true` in Preferences to log decisions without pressing any keys.
- If a Codex command wraps across several rows, YO approves it only when every reading of it is allowed.
- When Codex shows a capacity notice, YO types `continue` and Enter, then retries every 10 seconds while that notice is on screen. It never touches text you typed.

## Remote access

`boot.sh` and `yomux` listen on all interfaces by default, and every request requires a login. YOmux does not manage your firewall. Allow only the machines you trust:

```bash
sudo ufw allow from <client-ip> to any port 9998 proto tcp
```

Or keep YOmux local and use an SSH tunnel:

```bash
yomux --host 127.0.0.1 --port 9998
autossh -M 0 -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 9998:127.0.0.1:9998 user@server
```

## Several machines with one home directory

Run a separate YOmux on each machine, with its own local config and state directories and its own writable checkout. A shared home can hold read-only source or transcripts, but YOmux does not see tmux sessions on other machines, and SQLite databases must not be shared between hosts.

```bash
export YOMUX_CONFIG_DIR=/local/path/yomux-config
export YOMUX_STATE_DIR=/local/path/yomux-state
yomux --port 9998
```

Use absolute directories outside the checkout. Changing a path does not move existing data. YOmux refuses to keep its databases on a network filesystem; see [Storage roots](specs/BACKEND_ARCHITECTURE.md#storage-roots).

## Updates

When a newer version reaches `origin/main`, YOmux shows a notice. Admins can select Update & restart; YOmux builds and checks the new version first, then restarts and reloads open browsers, waiting while you have unsaved edits. Set `updates.notify_level` to `none` to turn off update checks. Details are in [Landing and releases](DEVELOPMENT.md#landing-and-releases).

## Upgrading from older versions

- Old `YOLOMUX_` environment variable names still work with a deprecation warning; when both are set, `YOMUX_` wins, even if it is empty.
- The old `--dang`, `--dangerously-yolo`, and `boot.sh --no-dang` options are accepted but do nothing. Choose auto-approve per session in File -> New.
- The Python server is gone. These Python-only features were not carried over: Search & Runs, background activity summaries, watched pull requests, hosted GitHub and Linear enrichment in YO!info, login decoys, firewall escalation, `--sessions`, and the Python development reloader. Choosing the Python backend (`--deprecated-python` or a Python `--backend`) is rejected. Your existing data is kept. See [Go backend status](../GO_MIGRATION.md).
