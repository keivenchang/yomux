# YOmux

YOmux is a browser workspace for managing AI work in tmux. Work with Claude and Codex sessions, browse repositories, edit files and Markdown, and follow agent activity from desktop or mobile.

Go owns the product runtime. Python is used only for developer tools and tests.

[Quickstart](#quickstart) · [First launch](#first-launch) · [Daily use](#daily-use) · [Authentication & roles](#authentication--roles) · [Remote access](#remote-access) · [Development guide](docs/DEVELOPMENT.md)

## Requirements

* Bazel 9.2.0 and a C compiler for native builds. Bazel selects Go 1.27.1 on Linux and macOS; a host Go installation is not required.
* Node.js and npm are needed to rebuild pinned editor bundles only when no verified bundle cache exists. Installed startup uses prepared assets without Node.js or upstream access.
* tmux and Git are runtime requirements. Install the Claude or Codex CLI to use those clients and YO!agent responses.
* Python 3.11+ is a developer-tool requirement. Product installation and startup do not require Python, a virtualenv, or a Python SDK.
* The server generates its default certificate with Go crypto. Only the optional local-CA setup helper requires `openssl`.
* Linux full-gate hosts need `fs.inotify.max_user_instances=1024`, `fs.inotify.max_user_watches=1048576`, and `fs.inotify.max_queued_events=65536`; see the development guide for persistent `sysctl` setup. Startup warns in the server log and browser when limits are low, reports measured values, and provides a command that raises only low limits until reboot.
* The full gate provides Docker and prepared qualified native execution with the same coverage inventory. macOS applies one Seatbelt policy to each test command and its descendants; setup and non-test build actions run without OS sandbox enforcement. See the development guide for preparation and isolation guarantees.

## Quickstart

Build and install the native runtime, then launch it on an explicit port:

```bash
git clone https://github.com/keivenchang/yomux.git
cd yomux
make setup
./boot.sh 9998
```

Open https://localhost:9998 and follow [First launch](#first-launch) to enable an account. The default certificate is self-signed. For another machine, see [Remote access](#remote-access).

Existing sessions on your Unix user's default tmux server appear in YOmux. If you need a session, create one in another terminal with `tmux new-session -A -s project1`. File -> New offers normal and dangerous auto-approve launches for Claude and Codex; choose the permission mode for each new session.

`make setup` uses Bazel and the native asset builder. Setup acquires checksum-pinned browser libraries, fonts, PDF.js, and Unicode/CLDR inputs, and builds the editors from npm lockfiles. Verified asset and editor caches support offline setup and repair. Browser bundles, locale copies, and the emoji catalog are generated outputs. Installed startup uses these prepared files without downloading dependencies. Source-checkout startup rebuilds the application bundles from prepared inputs, then checks the served JavaScript and CSS. Installed startup verifies packaged assets without needing the source checkout, Node.js, or upstream access. `make dev` separately installs Python test tools.

The installation records its source checkout in `install-source.json`. Keep that checkout available for self-update; normal startup uses installed files. Contributor and build instructions live in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## First launch

On first run YOmux creates `~/.config/yomux/auth.yaml` with every account commented out. An existing empty file is recovered into the same starter configuration with mode `0600`; populated regular files, including read-only configurations, are preserved. Symlinks and non-regular files are rejected. No login works until you uncomment one:

```bash
# edit the file — nano, vim, whatever you prefer
nano ~/.config/yomux/auth.yaml
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

### Launch options

Use an explicit port: `./boot.sh 9998` launches this checkout, and `yomux --port 9998` launches the installed copy. Installed commands also accept `YOMUX_PORT` or `YOMUX_DEFAULT_PORT`. An occupied port is refused unless `--force` is supplied; replacement still requires instance-identity checks.

* `--host` selects the bind address. Managed launches default to `0.0.0.0`; the Go binary run directly defaults to `127.0.0.1`. Use `./boot.sh --host 127.0.0.1 9998` for local-only access.
* HTTPS is the default. The server generates and reuses a self-signed certificate under `${TMPDIR:-/tmp}/yomux-go-tls-<port>/`. Supply `--cert` and `--key` together for an existing certificate, or `--http` for plain HTTP.
* `--backend go`, `--backend auto`, and `--golang` select Go. Python selections, `--deprecated-python`, and invalid backend values are rejected before product roots, leases, or listeners change. A missing or unexecutable Go binary fails without fallback. See [Retired Python behavior](#retired-python-behavior) for removed features and flags.

`YOMUX_*` is the canonical environment prefix. Legacy `YOLOMUX_*` inputs remain accepted with a deprecation warning. When both are present, `YOMUX_*` wins, including an explicitly empty value. Backend selection is checked in both namespaces before normalization; either namespace selecting Python or an invalid backend is rejected.

Operator launches use the same default tmux server as `tmux list-sessions` for the launching Unix user. Every session on that roster is eligible for discovery, regardless of its source checkout. Managed launchers clear inherited `YOMUX_TMUX_SOCKET`, `TMUX`, and `TMUX_PANE` so a nested client cannot select another server. Separately named `tmux -L` or `tmux -S` servers and other Unix users' servers remain independent. Direct entrypoints retain an explicitly supplied private socket for isolated tests and fixtures.

## Concepts

YOmux follows terminal-app terminology (iTerm2-style):

The capability catalog controls which tabs are available. Native YO!agent and YO!chat become available after their stores initialize; unavailable integrations remain hidden.

* **Pane** — a visible split region that holds one or more tabs and shows one at a time. Ordinary **Generic Panes** tile via draggable splits. Optional outermost **Side Panes** are narrow left/right specializations for Finder/Differ/Tabber and Side-created YO!\* tabs; their role is explicit and cannot be exchanged with a Generic Pane.

* **Tab** — the thing shown inside a pane. Tab types: **tmux session** (terminal), **Finder** (file browser), **Differ** (changed files), **Tabber** (recent tabs/windows), **Git history** (`Diff repo`), **File** (text editor, preview, diff, or image viewer), **Preferences**, **YO!agent**, and **YO!chat**.

When a Tab is a tmux session, that session has its own internal hierarchy — tmux sub-windows (`Ctrl-b n/p`) and tmux panes (`Ctrl-b %/"`) — which belong to tmux, not YOmux. Watch the overloaded word **pane**: a YOmux Pane is a browser layout split, a tmux pane is a split inside a tmux sub-window.

YOmux addresses tmux sessions with the exact target `=<session>:`. The leading `=` prevents tmux from falling back to a prefix or pattern match when a named session is absent; destructive and mutating actions therefore cannot select a similarly named sibling session.

## Daily use

Available tabs follow the server's capability catalog. Unavailable integrations stay hidden. See the [UI contract](docs/specs/GUI.md) for detailed interaction rules.

### Panes, tabs, and Tabber

Pinned tabs show their existing pin icon in Tabber and precede unpinned conversations, files, and tools. Pin and unpin update the list immediately, including while the pointer is inside Tabber.

* Click a tab to show it in that pane. Use the `Tabs` menu to activate minimized or inactive tabs.

* With a mouse, trackpad, or Pencil, hover a tab for details; right-click, Control-click, or press the keyboard Menu key/Shift-F10 for actions without switching to that tab. On pure-touch screens, long-press a tab for the same bottom action sheet; drag instead to cancel it. Split actions place that tab on the named side and retain a useful `Drop a tab here` peer pane. `Expand pane` temporarily fills the workspace and restores the exact prior layout when used again.

* Press `?` for the responsive Keyboard Shortcuts and Legends dialog, including the green play, yellow pause, and red stop status glyph meanings.

* Drag a tab between same-role pane tab bars, drop near a Generic Pane edge to split it, or drop on the outer root edge for a full-span pane. A generic tab moved to the far right creates another Generic Pane, not a Side Pane. No tab can move or swap between Side and Generic roles. Pane roles, edges, splits, and percentages encode into the shareable page URL. Pinned tabs stay in their pane, are never minimized or auto-evicted, and a full pane with no evictable unpinned/clean tab refuses incoming tabs with a visible status message instead of silently exceeding the per-pane tab cap.

* File -> `Finder/Differ/Tabber` opens the three independent file-surface tabs. Normal Tabber shows one flat, rounded row per conversation/window: the first line has the shared activity marker, agent symbol, tmux name, and muted activity date; the second line has its description.

Normal shows at most one content pane and hides its tab strip. The left Tabber owns select/unselect for conversations, file editors, Finder, Differ, and other tools; unselected items keep their buffers and view state. Select a row to show its content in the pane, select another row to switch content, or select the current row again to hide it. The empty Normal pane does not show the drag-and-drop prompt.

Normal and Advanced Tabber default to Ago dates and newest activity first. Existing browsers switch to Ago once after saved browser and URL settings load; later Date/Ago/None choices persist.

Rows keep their displayed order while the pointer is anywhere inside Tabber, including pane whitespace. Normal hides the topbar activity counts; Advanced keeps them visible. Activity markers and dates continue updating; the latest order applies after the pointer leaves. Sort and branch controls take effect immediately. Advanced restores the unrestricted pane and drag controls; existing installations keep their previous mode.

New Chat opens the existing client and shell choices and assigns the next available numeric tmux name. Right-click offers Rename Description before Rename tmux. Descriptions belong to each window, persist through reloads, restarts, and tmux renames, and a nonempty manual description overrides automatic conversation naming. Clearing a manual description immediately queues an AI summary when a recorded conversation event is within 30 minutes and Codex is available.

Automatic descriptions are enabled by default and summarize changed conversation text every 10 minutes using Codex gpt-6-luna with low effort, but only when a recorded conversation event occurred within the last 30 minutes. Idle conversations keep their existing description. Codex is preferred over Claude when available; when AI inference cannot run, no rename is made. AI-generated descriptions start with `[AI summary] ` and stay within 96 characters total; failed or timed-out requests retain the previous description. General Preferences has separate enable, model, effort, and interval controls.

In Advanced, at 900px and wider the available file surfaces live in an explicit narrow left Side Pane; a missing one recreates that Side Pane, and Side tabs never enter Generic Panes. Below 900px there is no Side Pane and File opens only the selected surface in the sole full-width Generic Pane. Widening restores the available file surfaces to the left while leaving YO!\* tabs generic.

* Tabber lists open tabs and tmux sub-windows by recent activity. `Mod+B` hides Finder/Differ/Tabber or restores the default left Side Pane on wide layouts. The top-bar language picker changes the live UI.

### Terminals and activity

Use the pane Info Bar to switch tmux sub-windows, choose among a session's repositories, open transcripts (`Tx`), or inspect the event log (`Log`). Tab and sub-window navigation acknowledges the chosen destination immediately; unavailable connections and stalled switches expose Retry controls.

Attention badges identify agents waiting for input or approval even when YO is off. Status follows the current Claude or Codex process in each tmux pane; old spinner history, completed work, and ordinary footer text do not create duplicate activity.

* The browser title, favicon badge, and topbar activity count report working Claude/Codex sub-windows, so two active agents inside one tmux session count as two everywhere.

### Copying terminal text

* Select text and press `Cmd-C` (Mac) / `Ctrl-C` (PC) to copy it to your browser clipboard. While a full-screen app like Claude owns the mouse, a normal drag goes to the app instead of making a selection — hold `Option` (Mac) / `Shift` (PC) and drag to force a real terminal selection, or just select inside the app: its own copy (sent as an OSC 52 escape) is forwarded to your browser clipboard automatically (the status line shows `copied N chars`).

* `Cmd-C` with nothing selected does nothing — it is never delivered to the running program. Plain `Ctrl-C` with nothing selected still sends `SIGINT` to interrupt the program.

* To copy the tmux copy-mode selection (server-side, via tmux), press `Cmd-Option-C` (Mac) / `Ctrl-Alt-C` (PC), or right-click and choose `Copy tmux selection`.

* Right-click keeps the current selection highlighted and offers `Copy` / `Copy without indent`. When Claude owns the visible highlighted block and sends it through OSC 52, the right-click menu must preserve that app-side block; it must not re-read and copy only the small text under the cursor.

* Right-clicking a URL in a terminal pane or rendered markdown puts `Open URL in a new tab` first, then `Copy URL`; when the visible selected text differs from the actual href, the menu labels that path explicitly as `Copy selected text`.

* After a terminal copy/open action consumes selected text, YOmux clears stale browser/xterm selection. Explicit `Copy tmux selection` also exits tmux copy-mode after copying so selected rows do not stay painted as green blocks.

### Finder, Differ, and Git history

Finder accepts any absolute path accessible to the server process. Reading file contents, comparisons, and commit patches requires an administrator account. OS permissions, descriptor identity checks, bounded operations, and the protected product-root mutation guard apply; the legacy Python configured-root and credential-name denylist does not.

Finder always annotates the live uncommitted working tree from `HEAD` to the current files, including staged, unstaged, deleted, renamed, and untracked changes; only Differ follows its selectable FROM/TO comparison, so changing those refs does not reload or alter Finder counts.

Finder Sync remembers each session's root, manual expansion choices, selection, and touched paths; touched ancestors carry that session's `★`.

Sync automatically opens only the selected repository, or the common parent of touched paths when no repository is known, plus the ancestors needed to reach it. It leaves child directories closed even when they contain edited files or the session's working directory. With no touched paths or repository, it uses the working directory. Explicit manual opens and file reveals can open deeper paths.

Switching sessions paints the shared bounded cache immediately, then revalidates visible directories in the background. Filesystem permission failures are reported in Finder instead of terminating the request. Finder accepts any accessible absolute root and applies its descriptor identity, bounded-work, role, and protected-root mutation checks.

* Drag a Finder or Differ file row into a pane to open that file there; dropping near a pane edge opens it in a new split.

* Right-click a file path printed in a terminal to open its menu immediately. The menu shows a disabled loading row while YOmux confirms an uncached path, then replaces that row with **Open file** and **Copy path**. A missing path simply removes the loading row. Opening a confirmed file creates its file tab before optional Git details finish, so slow history or Blame/Diff enrichment does not hold back the document. Reopening an already-open file, including through a resolved alias, keeps that tab's current Edit, Preview, or Diff mode; only an explicit mode action changes it. Wrap remains a global editor preference.

Right-click or long-press a Git directory and choose `ΔShow Diff` for paged commit history. Commits appear before their file totals finish loading. Expand a commit for its message and changed files, then select a file for its historical Diff, immutable Preview, and read-only Edit. A file row instead offers `Edit in new tab`, `Preview in new tab`, and `ΔShow Diff`; these reuse its canonical working-tree editor tab and select the requested mode, preserving dirty content and pane placement.

Finder's `rwx` control beside Date/Ago shows Unix permissions and remembers the choice. Right-click a file or folder and select **Get Info** for filesystem metadata and cancellable folder contents totals. Administrators can apply Owner, Group, and Others permissions when the server's OS account permits it. Applying to enclosed items requires a separate confirmation and applies the exact selected bits, including execute; Cancel preserves changes already completed. Symlink permissions are read-only. Unavailable creation times remain unavailable, and incomplete folder totals are marked partial.

Differ prepares file attribution in a private SQLite index under the instance state directory. Requests use prepared history and enqueue background catch-up instead of scanning transcript archives. Saved results remain visible during refresh, and incomplete preparation displays “Indexing history.” The index stores normalized file operations and progress, without transcript bodies or shell commands. Stats and attribution retain independent durable positions through the shared in-process transcript reader.

### Files and Markdown

New TextEditor and ViewEditor cursors default to Line. An explicitly saved Block preference is preserved. Reload also retries referenced Markdown images, including unchanged documents and retained ViewEditor images. It uses the referenced path and does not guess renamed directories.

Text files provide TextEdit for raw source editing. Markdown also provides ProseMirror-powered ViewEdit. In Split mode the left surface is labeled TextEditor and the right rendered Markdown WYSIWYG surface is labeled ViewEditor. Typing stays local to the active surface, then synchronizes both surfaces and canonical Markdown after 1.25 seconds without further input; saving, blur, mode changes, and close flush immediately. In ViewEditor, ordinary Enter creates a Markdown paragraph break; Shift-Enter alone creates an explicit hard break. Markdown image paste saves the image in a directory named for the Markdown file without its extension, then inserts a relative image reference at the original caret or selected range: `/a/b/file.md` becomes `/a/b/file/upload.png` and `![upload](file/upload.png)`.

* Markdown previews load local images through the authenticated file API, which authorizes one descriptor-bounded direct response instead of creating a background-job artifact. A missing login, missing path, or over-limit file produces a distinct message with Open and Download actions instead of a generic broken-image label. Numbered task labels such as `- [x] 2. Verify it` keep the checkbox, number, and text on one line without rewriting the Markdown; genuine nested multi-item lists remain indented, and clicking the checkbox still updates its source line.

* Upload or paste files with drag-drop, clipboard paste, or the `+` button. Dropping a file on a terminal offers actions suited to an AI or shell pane; the numbered upload chooser opens at the pane top-left without a separate Uploaded toast. Terminal uploads are transient artifacts stored at `/tmp/yomux.<login-user>/uploads/<session>/`, where the login user is the authenticated YOmux username rather than the Unix account. Each user tree is private (`0700`), files are removed lazily after seven days by default, and `uploads.retention_days` in `~/.config/yomux/settings.yaml` accepts 1–365 days. Markdown editor image paste uses separate document storage: `/a/b/file.md` creates `/a/b/file/`, writes the image there, and inserts a relative `file/<name>` reference at the captured editor selection.

### LaTeX previews

The TeX compiler prompt uses a left-aligned status card, normal UI text, wrapped command instructions, and wrapping Install and Retry actions. Installation still requires consent and retains progress and errors.

LaTeX documents (`.tex` and `.ltx`) provide TextEdit, Preview, and Split. Preview compiles the current editor buffer to PDF without saving it. YOmux detects Tectonic or pdfLaTeX on the server; if missing, Preview asks before downloading a checksum-verified Tectonic compiler into this instance’s private tools directory, without sudo. The first compilation may also download TeX packages. Failed installation shows manual instructions and Retry. Compiler execution restricts reads to the document folder and required runtime files, disables shell escape, and confines writes to private jobs and cache. Linux needs bubblewrap (`sudo apt install bubblewrap`); macOS uses its built-in sandbox. Compilation errors appear in Preview.

### Quick Open and indexing

Use `Mod+P` for Quick Open. Clean file tabs whose paths were deleted stay hidden; dirty buffers remain reachable, and clean tabs return when the file reappears.

* Go Quick Open uses a private SQLite filename index. Directory ancestry is shared across overlapping roots; file contents stay outside the index. The default `index_persist_max_mb` is 1024 MiB (1,073,741,824 bytes) for the combined active database and WAL across all roots, with a 2048 MiB transient ceiling for replacement. Retained rollback files are reported separately. A replacement that cannot fit keeps the prior searchable generation and reports the blocked refresh; a cold root may publish measured partial coverage. Lowering the budget can leave retained data over the new limit until a compliant replacement succeeds. Disabling `index_persist` switches to memory without deleting disk snapshots or rollback files.

* Go loads persisted indexes without immediately crawling every configured root. Explicit searches retain cached results and their refresh ownership when panels hide. Visible scopes use `file_explorer.index_refresh_seconds` (60 seconds by default); hidden roots use `index_background_refresh_seconds` (900 seconds / 15 minutes). Zero disables periodic work for that tier. Searches remain available, and rejected replacements clean up their own unpublished staging files while preserving searchable generations.

Settings changes to budgets, file limits, and refresh cadence retain compatible indexed results during reconciliation. Root and exclusion changes fence affected results until acknowledged; malformed YAML retains the last valid configuration. Index status reports revisions, coverage, synchronization errors, and storage usage. Dependency, cache, secret, VCS, build, native executable, object, library, and bytecode entries are excluded from indexing; ordinary Finder listings remain available.

### Agents, chat, and notifications

Preferences changes reach other browsers through the shared event stream. Reconnecting, returning to a hidden tab, or recovering after a server restart fetches missed settings without reloading the page.

Automatic Tabber descriptions have separate Preferences controls for enabling generation, the Codex model, reasoning effort, and refresh interval. Defaults are enabled, gpt-6-luna, low effort, and 10 minutes. Changed transcripts are summarized per physical tmux window only when their latest recorded conversation event is within 30 minutes; unchanged or idle transcripts do not invoke the model. Summaries use a 60-second timeout and carry the `[AI summary] ` prefix within the 96-character limit; unavailable inference does not rename the window. The active supported agent pane supplies the transcript, with the lowest pane index as fallback. Instances coalesce jobs; manual edits and conversation changes supersede old results. Failures retain the last description. Clearing a manual description queues a summary immediately when recent activity is within 30 minutes and Codex is available.

* Native YO!agent provides conversations, skills, jobs, session watches, and reviewed sends. Responses use the installed Claude or Codex CLI. Model-proposed sends require confirmation. See [`docs/YOAGENT.md`](docs/YOAGENT.md) for configuration and send rules.

* File -> `YO!chat` opens the native shared conversation for authenticated users within the selected local deployment. It provides persisted history, search, typing presence, and `/yo` questions through YO!agent. The server enforces the configured retention policy. Cross-host room sharing is unsupported.

* Cross-pane notifications appear in one global toast rail and identify their target tab without changing your current focus. Attention remains until acknowledged; completion, chat, PR, and job notices are coalesced by target. Clicking a notice opens its target and clears it. Uploads and file/editor errors remain in the pane where that direct action occurred. Preferences independently control in-YOmux and system notifications.

### Mobile and diagnostics

On touch terminals, tap to focus native input and open the device keyboard. A vertical finger pan scrolls tmux history without opening the keyboard; alternate-screen applications retain their mouse-wheel or cursor-key routing. The movable smart-key palette provides Esc, modifiers, cursor keys, Page Up/Down, and Ctrl-C. More switches pages and X closes it while remembering its position.

In Codex, ordinary wheel or trackpad scrolling moves through the conversation while keeping the prompt fixed. Page Up/Down (Fn+Up/Down on Mac) sends native application keys. Hold Shift while scrolling, or use Shift+Page Up/Down, to browse tmux history; Esc exits tmux copy mode.

* Phone layouts show the selected tab in one full-width pane while preserving the restored desktop split for wider viewports. Editor, Preview, and Diff actions use touch-sized versions of the existing controls; Markdown image paste/upload and pane switching stay on the same desktop action paths. Once a finger claims Preview scrolling, deferred render, zoom, split-reflection, and layout restoration work yields to that live position instead of snapping backward.

* File -> `YO!stats` uses the shared `Graphs`, `Cost`, `API/SSE`, `Daemons`, and `Logs` shell when `pane.stats.v1` is available. The browser uses server capabilities and complete snapshots; backend parity gaps are listed in [`GO_MIGRATION.md`](GO_MIGRATION.md).

* The pane header pop-out button opens supported file previews in a detached browser window. YO!stats opens in a live, single-pane window without YOmux menus or Finder/Differ/Tabber or a minimize control; its controls stay interactive, and its tab leaves the source layout while detached and returns to its prior slot when the window closes.

* For a real-device mobile diagnostic, open YOmux with `?debug=1`, reproduce the Preview or terminal input problem once, then open `YO!stats` -> `API/SSE` and press `Copy Events`. That debug-only action copies raw JSON containing the Preview touch/viewport/write owners and generations plus terminal tap/focus/input stages; normal sessions do not retain this mobile trace.

## Two machines sharing a home

Run one independent YOmux deployment per machine. Give every running instance private host-local configuration and state roots and its own writable worktree. A mounted home may supply read-only source or transcripts; it does not provide cross-host tmux discovery or a shared writable database.

```bash
export YOMUX_CONFIG_DIR=/local/path/yomux-config
export YOMUX_STATE_DIR=/local/path/yomux-state
yomux --port 9998
```

Use absolute child directories outside the checkout. The launcher rejects unsafe roots before creating state or replacing a listener. Existing profiles retain their recorded roots; changing a path does not migrate its contents.

| Root | Current Go ownership | Sharing rule |
| --- | --- | --- |
| `YOMUX_CONFIG_DIR` | Authentication, settings, event and operation journals, background-owner records, and the default stats database | Give each instance a private local directory. A lifetime lease prevents two owners from sharing the journals. |
| `YOMUX_STATE_DIR` | Session-files cache, credential-admission storage, and native chat and agent state | Give each instance private local state. Do not share live SQLite databases between hosts. |
| Writable checkout | Source, selected binary, and generated browser assets | Use separate worktrees. The checkout writer declaration coordinates cooperating same-host writers. |

The retired Python service/runtime/cache topology and automatic database partitioning are not the Go storage contract. The native chat store retains its host-partitioned path inside the selected state root; that path does not make network filesystem sharing safe. Go admits its journals and auth/stats WAL stores only on supported local filesystems unless the explicit emergency override is set. The override does not make WAL-on-NFS safe. SQLite documents this restriction in its [WAL overview](https://www.sqlite.org/wal.html#overview).

Retirement removes source code, not user state. Preserve existing databases, configuration, backups, caches, user skills, and agent configuration. Do not move a database or copy its WAL/SHM sidecars independently while a writer is running. Any deliberate migration needs a consistent backup, a stopped source and destination owner, and a verified restoration procedure. Go checkpoint migration backups and the remaining platform checks are described in [`GO_MIGRATION.md`](GO_MIGRATION.md).

## Authentication & roles

The Go server applies the route catalog's role policy before handling authenticated requests. Browser login, Basic authentication, and terminal WebSocket authentication share credential admission.

| Role | Can do |
| --- | --- |
| `admin` | Type into tmux panes, create sessions, upload files, toggle `YO`, switch tmux sub-windows, and configure native YO!agent. |
| `readonly` | View panes, transcripts, branch metadata, logs, YOLO status, and Repo Diff history, statistics, and changed-file lists. Terminal input and arbitrary filesystem contents require an administrator account. |

Read-only access shares live panes and repository metadata. Arbitrary filesystem contents, file comparisons, commit patches, writing files, uploading, and sending terminal input require `admin` access. Finder listings, metadata, and filename searches remain available to readonly users.

Browser writes, login submissions, logout, and terminal connections reject another scheme, host, or port. Normal page navigation and command-line requests without browser-origin headers remain supported. Terminating a session requires `POST /api/kill-session`.

Login cookies have a 90-day lifetime and use a port-scoped name backed by the configuration directory's secret. Changing an account password changes its cookie signature input and invalidates existing cookies. Go credential admission covers form, Basic, and WebSocket authentication; unavailable admission storage fails closed before password hashing. Python recovery commands, throttle diagnostics, login decoys, and firewall escalation are retired.

## Agent permissions & YOLO

### Choosing permissions for a new session

File -> New always offers both normal and `dangerously auto approve` choices for Claude and Codex. Normal launches use plain `claude` or `codex`. Dangerous launches use:

```bash
claude --dangerously-skip-permissions
codex --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust
```

Claude's flag bypasses permission prompts while retaining hooks and OAuth login. Codex's first flag bypasses command approval and the command sandbox; its hook-trust flag separately allows enabled hooks to run without persisted hook trust.

Choose the mode for each session in the menu. There is no server-wide permission setting. Legacy `--dang` and `--dangerously-yolo` inputs, plus `boot.sh --no-dang`, are accepted only for compatibility and have no effect. Requests without an explicit dangerous launch selection use normal permissions. Administrator, client availability, login, and session-limit checks still apply.

### Existing-session YO toggle

The `YO` toggle watches the visible screen of an existing session and applies configured approval rules. Choosing a dangerous launch does not bypass these rules or change another session. Rules live in `~/.config/yomux/yolo-rules.yaml`:

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

The `tmux` menu has `Open rule file` and `Reload rules`. Set `yolo.dry_run: true` in Preferences to record intended actions without sending terminal input. The optional `risk:` label uses `read`, `edit`, `network`, `process`, `delete`, `credential`, or `unknown`; other strings are accepted but not standardized.

YO also retries the exact current Codex capacity notice immediately with `continue` and Enter, then every 10 seconds while the notice remains current. If its own `continue` stays unsubmitted, the next retry sends Enter only. User drafts remain untouched; newer output, active work, or an unrecognized footer stops retries. See [GO_MIGRATION.md](GO_MIGRATION.md#terminal-and-sessions) for the detailed contract.

## Remote access

The managed Go launcher binds `--host 0.0.0.0` (all interfaces) by default: YOmux supports reaching sessions from another machine on a trusted LAN, and every product request is gated by login. The Go binary itself defaults to `127.0.0.1` when run directly. Restrict remote access with a firewall or tunnel. The server does not manage firewall rules. For example, allow a trusted client IP:

```bash
sudo ufw allow from <client-ip> to any port 9998 proto tcp
```

To keep YOmux local-only instead, bind loopback and tunnel from your client:

```bash
yomux --host 127.0.0.1 --port 9998
autossh -M 0 -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 9998:127.0.0.1:9998 user@server
```

## Runtime architecture

### Go backend

One `yomux-go` process owns HTTPS and authentication, terminal PTY/WebSocket transport, events and operation receipts, Finder and Differ, background queues, YO!stats, chat, and agent handlers. Finder may start a private native index worker. Linux uses native filesystem events with bounded reconciliation; other platforms use polling.

`yomux-go --operator` owns launch planning, root and executable checks, listener identity, readiness, session discovery, profiles, and native asset publication. Explicit profiles retain their configuration, state, certificates, and ports. Self-update verifies the recorded source checkout, builds the selected revision, refreshes the installation prefix, and restarts the installed binary.

Unknown `/api/*` routes return typed `501 feature_unavailable` errors. [GO_MIGRATION.md](GO_MIGRATION.md) owns routes, capabilities, migration status, and remaining gaps. The [backend architecture guide](docs/specs/BACKEND_ARCHITECTURE.md) describes process and storage ownership.

`make setup` and the native build fetch the checksum-pinned PDF.js preview dependency into the ignored `static/vendor/pdfjs/` directory. Verified local assets and the archive cache work offline; download failures preserve existing assets and report how to recover.


## Optional developer tools

For contributor checks, `make dev` installs `mypy`, `pillow`, `pytest`, `pytest-timeout`, `pytest-xdist`, and `selenium`. See [Development](docs/DEVELOPMENT.md) for the gate and browser prerequisites.

Contributor setup, build commands, test dependencies, Docker and qualified-native execution, and manual preview workflows live in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). Use `make dev` for the separate Python developer tools and `python3 tools/check.py` for the canonical gate. Automated tests use fixture-owned state and ports; they never connect to the operator runtime on port 7220. Port 7221 is reserved for manual Go previews.

Linux full-gate hosts require `fs.inotify.max_user_instances=1024`, `fs.inotify.max_user_watches=1048576`, and `fs.inotify.max_queued_events=65536`.

Browser source lives in `static_src/`. Generated `static/yomux.js` and `static/yomux.css` are ignored outputs; tracked xterm vendor assets ship with the runtime. `make build` publishes the native binary and generated assets.

Python prompt-analysis helpers, tmux-wall diagnostics, mocks, fixture builders, and pytest controllers remain source-only tools. AI-agent conventions live in [AGENTS.md](AGENTS.md), and [docs/README.md](docs/README.md) indexes specifications and grouped release notes. Git tags remain the authority for exact release contents.

## Retired Python behavior

The Python server, `--sessions`, transcript/runtime report modes, the Python development reloader, optional login decoys, firewall escalation, and unavailable integrations are retired. Unsupported API routes return typed unavailable responses. [Launch options](#launch-options) lists the supported native selectors.

Search & Runs event search and compact run history, background AI activity summaries, watched pull requests, and hosted GitHub/Linear enrichment in YO!info are retired Python-only behavior. A remaining browser panel or saved layout does not supply those services. Native terminal transcripts, per-session event logs, local Finder/Git views, chat, and YO!agent conversations remain separate supported features.

## License

YOmux is licensed under PolyForm Noncommercial 1.0.0. Noncommercial use is allowed under that license. Commercial use requires a separate commercial license from Keiven Chang.

Third-party code and generated dependency bundles keep their own upstream notices; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
