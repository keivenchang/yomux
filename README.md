# YOmux

Lightweight, powerful browser workspace for managing AI work.

Text files provide TextEdit for raw source editing. Markdown also provides ProseMirror-powered ViewEdit. In Split mode the left surface is labeled TextEditor and the right rendered Markdown WYSIWYG surface is labeled ViewEditor. Typing stays local to the active surface, then synchronizes both surfaces and canonical Markdown after 1.25 seconds without further input; saving, blur, mode changes, and close flush immediately. In ViewEditor, ordinary Enter creates a Markdown paragraph break; Shift-Enter alone creates an explicit hard break. Markdown image paste saves the image in a directory named for the Markdown file without its extension, then inserts a relative image reference at the original caret or selected range: `/a/b/file.md` becomes `/a/b/file/upload.png` and `![upload](file/upload.png)`.

Go owns the product runtime. `make setup` builds the native server, publishes browser assets, and installs `yomux` and `yolomux` below `PREFIX` (default `~/.local`). Installed commands execute the binary and assets under `PREFIX/lib/yomux`. Their `install-source.json` records the verified Git checkout for self-update. Startup uses the installed files; updates require the recorded checkout to remain available. Python remains available for developer tools, mocks, fixture builders, and test controllers; it is not a product installation or startup prerequisite.

Contributor and build instructions live in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md). AI-agent conventions live in [`AGENTS.md`](AGENTS.md), the concise UI contract and grouped release notes are indexed in [`docs/README.md`](docs/README.md).

## Requirements

* Bazel and a C compiler for native builds; tmux and Git for the runtime. Python 3.11+ is only a developer-tool requirement.

* `openssl` only for the optional local-CA setup helper. The server generates its default certificate with Go crypto.

* Linux full-gate hosts: `fs.inotify.max_user_instances=1024`, `fs.inotify.max_user_watches=1048576`, and `fs.inotify.max_queued_events=65536`; see the development guide for the persistent `sysctl` setup.

* Go builds: Bazel 9.2.0. The full gate provides Docker and prepared qualified native execution with the same coverage inventory. macOS applies one Seatbelt policy to each test command and its descendants. Setup and non-test build actions run without OS sandbox enforcement. See the development guide for preparation and isolation guarantees. Bazel selects Go 1.27.1 on both platforms; a host Go installation is not required for the canonical build.

## Quickstart

Build and install the native runtime, then select a port. See [Launch options](#launch-options) for backend aliases and [Retired Python behavior](#retired-python-behavior) for removed options.

```bash
git clone https://github.com/keivenchang/yomux.git
cd yomux
make setup          # native build, asset generation, and command installation
tmux new-session -A -s project1     # optional: create one if you do not already have tmux sessions
./boot.sh 9998
```

`make setup` uses Bazel and the native asset builder. No virtualenv, Python SDK, npm installation, or Python dependency installation is required. Generated `static/yomux.js` and `static/yomux.css` remain ignored outputs. The tracked xterm vendor assets ship with the runtime. `make dev` separately installs Python test tools.

### Go backend

The Go backend is a modular monolith, not a port of Python's web-process-plus-local-daemons topology. One `yomux-go` process owns HTTPS/auth, the tmux PTY/WebSocket path, events and durable operation receipts, Finder and Differ, bounded in-process work queues, and the Go YO!stats writer/readers/materializers and collectors. It serves the shared browser bundle and renders home, login, and setup pages through embedded Go templates. Settings, localization, agent discovery, and bootstrap data also use native owners. Native launch owners perform planning and verification. Unavailable integrations are retired. Bazel and self-update generate browser assets through the native builder.

Available Go routes include terminal/session, Finder, Git, Differ, editor, diagnostics, YO!stats, and existing-session YO auto-approval. `yolo.auto-approve.v1` owns the authenticated YO toggle and configured approval rules for supported Claude and Codex panes. Go also retries the exact visible Codex capacity notice immediately with bracketed paste of exact `continue` followed by a real Enter key, then every 10 seconds while that notice remains current. The background-terminal count, `Goal achieved` label in the model footer, and shortcuts/warnings footer do not hide that notice. YO leaves user drafts untouched and stops retrying when newer output, active work, or an unrecognized footer makes the screen unsafe to submit. If its own `continue` is still unsubmitted, the next retry sends Enter only, and `yolo.dry_run` records intended actions without sending terminal input. YO!chat and YO!agent use native Go storage and handlers; their capabilities are enabled after their stores initialize. Agent responses use the installed Claude or Codex CLI. Unknown `/api/*` routes return typed `501 feature_unavailable` errors. [`GO_MIGRATION.md`](GO_MIGRATION.md) owns route, capability, and migration status.

Go Finder accepts any absolute path the server process can access. It does not apply the legacy Python backend's configured-root or credential-name denylist. OS permissions, descriptor identity checks, bounded operation limits, role checks, and the protected product-root mutation guard still apply.

Build, validate, and then start the preview:

```bash
make build
tools/run_bazel_tests.sh --jobs "$(getconf _NPROCESSORS_ONLN)"
./boot.sh 7221
```

### Launch options

`boot.sh <port>` runs the managed lifecycle. `--backend go`, `--backend auto`, and `--golang` select Go. Python selections through either environment prefix, `--deprecated-python`, and invalid backend values are rejected before product roots, leases, or listeners change. A missing or unexecutable Go binary fails without fallback. Removed features and flags are listed under [Retired Python behavior](#retired-python-behavior).

All YOmux operator launches (`boot.sh`, `make run`, `yomux-dev-start`, the installed `yomux` command, the Go binary, and the tmux companion tools) use the same global tmux server as `tmux list-sessions` for the launching Unix user. Here, “global” means that user's default server: every session on that roster is eligible for discovery, regardless of which checkout created it; separately named `tmux -L`/`-S` servers are independent and are not merged into one roster. Managed launchers clear inherited `YOMUX_TMUX_SOCKET`, `TMUX`, and `TMUX_PANE`, so starting YOmux from inside another tmux client cannot silently select that client's server. Direct entrypoints preserve a nonempty `YOMUX_TMUX_SOCKET` only when an isolated test or fixture explicitly supplies it; otherwise they apply the same global-server normalization. A different Unix user's tmux server is outside the user's permissions and is not implicitly merged. On macOS, `yomux-services` is only the private supervisor socket for the wrapper; the YOmux process inside it still uses the global server.

The Go process generates and reuses its own self-signed certificate under `${TMPDIR:-/tmp}/yomux-go-tls-<port>/`; it does not reuse Python's state-root certificate or local-CA helper. The managed launcher forwards host, port, checkout root, environment-selected config/state, `--http`, `--cert`, `--key`, and `--dang` as Go's `--dangerously-yolo`. Python-only `--sessions`, transcript-report, runtime-report, and `--dev` options return an error on Go rather than being silently dropped.

`YOMUX_*` is the canonical environment prefix. Legacy `YOLOMUX_*` inputs remain accepted with a deprecation warning; when both forms are present, `YOMUX_*` wins, including an explicitly empty value. Backend selection is checked in both namespaces before normalization; either namespace selecting Python or an invalid backend is rejected. Legacy environment support is reviewed separately and remains while supported callers need it. The installed `yolomux` command remains a native alias. The root Python entrypoints are removed.

Automated Go validation uses the Docker or qualified native backend with fixture-owned test ports and Chromium. Port 7221 is reserved for a manual smoke after those checks. Automated checks never connect to the operator runtime on port 7220; use the managed operator workflow for an explicitly requested live restart or smoke.

Contributors should use `make dev`. Its `dev` extra installs `mypy`, `pillow`, `pytest`, `pytest-timeout`, `pytest-xdist`, and `selenium` for the Python gate. Browser lanes additionally require Chrome or Chromium plus a compatible chromedriver; the xterm provenance check requires Node.js and npm. [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) lists the development checks and Go test lane.

Go uses Linux native filesystem events and bounded reconciliation; other platforms use polling. The old watchfiles shim and Python daemon topology are retired.

## Runtime architecture

The [Go backend](#go-backend) owns product requests and background work. Finder may start a private native index worker.

`yomux-go --operator` owns root planning, captured `unset`/`assign` plans, executable/version checks, listener census, authenticated readiness, session discovery, profile dispatch, and native asset publication. Mutable roots and existing persistence formats remain instance-owned. Explicit profiles preserve their configuration, state, certificates, and ports. Installation records the source checkout separately from the installed resource root. Self-update verifies that source, builds the selected revision with native tools, refreshes the same installation prefix, and restarts the installed binary.

Runtime JSON lives in `assets/runtime/`; built-in agent skill/context files remain packaged as data. `make build` publishes generated browser assets under the shared checkout writer declaration. The private-output `tools/staticgen` executable remains the Bazel and self-update builder.

## First launch

On first run YOmux creates `~/.config/yomux/auth.yaml` with every account commented out. No login works until you uncomment one:

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

## Two machines sharing a home

Run one independent YOmux deployment per machine. Give every running instance private host-local configuration and state roots and its own writable worktree. A mounted home may supply read-only source or transcripts; it does not provide cross-host tmux discovery or a shared writable database.

```bash
export YOMUX_CONFIG_DIR=/local/path/yomux-config
export YOMUX_STATE_DIR=/local/path/yomux-state
yomux --port 9998 --dang
```

Use absolute child directories outside the checkout. The launcher rejects unsafe roots before creating state or replacing a listener. Existing profiles retain their recorded roots; changing a path does not migrate its contents.

| Root | Current Go ownership | Sharing rule |
| --- | --- | --- |
| `YOMUX_CONFIG_DIR` | Authentication, settings, event and operation journals, background-owner records, and the default stats database | Give each instance a private local directory. A lifetime lease prevents two owners from sharing the journals. |
| `YOMUX_STATE_DIR` | Session-files cache, credential-admission storage, and native chat and agent state | Give each instance private local state. Do not share live SQLite databases between hosts. |
| Writable checkout | Source, selected binary, and generated browser assets | Use separate worktrees. The checkout writer declaration coordinates cooperating same-host writers. |

The retired Python service/runtime/cache topology and automatic database partitioning are not the Go storage contract. The native chat store retains its host-partitioned path inside the selected state root; that path does not make network filesystem sharing safe. Go admits its journals and auth/stats WAL stores only on supported local filesystems unless the explicit emergency override is set. The override does not make WAL-on-NFS safe. SQLite documents this restriction in its [WAL overview](https://www.sqlite.org/wal.html#overview).

Retirement removes source code, not user state. Preserve existing databases, configuration, backups, caches, user skills, and agent configuration. Do not move a database or copy its WAL/SHM sidecars independently while a writer is running. Any deliberate migration needs a consistent backup, a stopped source and destination owner, and a verified restoration procedure. Go checkpoint migration backups and the remaining platform checks are described in [`GO_MIGRATION.md`](GO_MIGRATION.md).

## Concepts

YOmux follows terminal-app terminology (iTerm2-style):

The capability catalog controls which tabs are available. Native YO!agent and YO!chat become available after their stores initialize; unavailable integrations remain hidden.

* **Pane** — a visible split region that holds one or more tabs and shows one at a time. Ordinary **Generic Panes** tile via draggable splits. Optional outermost **Side Panes** are narrow left/right specializations for Finder/Differ/Tabber and Side-created YO!\* tabs; their role is explicit and cannot be exchanged with a Generic Pane.

* **Tab** — the thing shown inside a pane. Tab types: **tmux session** (terminal), **Finder** (file browser), **Differ** (changed files), **Tabber** (recent tabs/windows), **Git history** (`Diff repo`), **File** (text editor, preview, diff, or image viewer), **Preferences**, **YO!agent**, and **YO!chat**.

When a Tab is a tmux session, that session has its own internal hierarchy — tmux sub-windows (`Ctrl-b n/p`) and tmux panes (`Ctrl-b %/"`) — which belong to tmux, not YOmux. Watch the overloaded word **pane**: a YOmux Pane is a browser layout split, a tmux pane is a split inside a tmux sub-window.

YOmux addresses tmux sessions with the exact target `=<session>:`. The leading `=` prevents tmux from falling back to a prefix or pattern match when a named session is absent; destructive and mutating actions therefore cannot select a similarly named sibling session.

## Daily use

Open YOmux after setup. Existing tmux sessions appear as tabs. The list describes shared browser behavior where the selected backend advertises the required capabilities; unsupported Go capabilities stay hidden. See [`docs/specs/GUI.md`](docs/specs/GUI.md) for durable interaction rules.

* Click a tab to show it in that pane. Use the `Tabs` menu to activate minimized or inactive tabs.

* Tab and tmux sub-window navigation acknowledges immediately even when the server is slow: cold tabs show a connecting/unavailable Retry state over the chosen pane, window switches feel as instant as native tmux — a brief opaque `Loading <index:name>…` mask bridges only the click-to-confirmation race and lifts as soon as the switch command succeeds and the repainted screen crosses a paint frame, typically well under half a second (only a genuinely hung switch degrades to an explicit `Still loading…` state with Retry/Cancel instead of flashing the previous window), and repeated network failures surface one compact topbar retry indicator instead of making local controls appear frozen.

* With a mouse, trackpad, or Pencil, hover a tab for details; right-click, Control-click, or press the keyboard Menu key/Shift-F10 for actions without switching to that tab. On pure-touch screens, long-press a tab for the same bottom action sheet; drag instead to cancel it. Split actions place that tab on the named side and retain a useful `Drop a tab here` peer pane. `Expand pane` temporarily fills the workspace and restores the exact prior layout when used again.

* Press `?` for the responsive Keyboard Shortcuts and Legends dialog, including the green play, yellow pause, and red stop status glyph meanings.

* Drag a tab between same-role pane tab bars, drop near a Generic Pane edge to split it, or drop on the outer root edge for a full-span pane. A generic tab moved to the far right creates another Generic Pane, not a Side Pane. No tab can move or swap between Side and Generic roles. Pane roles, edges, splits, and percentages encode into the shareable page URL. Pinned tabs stay in their pane, are never minimized or auto-evicted, and a full pane with no evictable unpinned/clean tab refuses incoming tabs with a visible status message instead of silently exceeding the per-pane tab cap.

* Drag a Finder or Differ file row into a pane to open that file there; dropping near a pane edge opens it in a new split.

* Right-click a file path printed in a terminal to open its menu immediately. The menu shows a disabled loading row while YOmux confirms an uncached path, then replaces that row with **Open file** and **Copy path**. A missing path simply removes the loading row. Opening a confirmed file creates its file tab before optional Git details finish, so slow history or Blame/Diff enrichment does not hold back the document. Reopening an already-open file, including through a resolved alias, keeps that tab's current Edit, Preview, or Diff mode; only an explicit mode action changes it. Wrap remains a global editor preference.

* Markdown previews load local images through the authenticated file API, which authorizes one descriptor-bounded direct response instead of creating a background-job artifact. A missing login, missing path, or over-limit file produces a distinct message with Open and Download actions instead of a generic broken-image label. Numbered task labels such as `- [x] 2. Verify it` keep the checkbox, number, and text on one line without rewriting the Markdown; genuine nested multi-item lists remain indented, and clicking the checkbox still updates its source line.

* Upload or paste files with drag-drop, clipboard paste, or the `+` button. Dropping a file on a terminal offers actions suited to an AI or shell pane; the numbered upload chooser opens at the pane top-left without a separate Uploaded toast. Terminal uploads are transient artifacts stored at `/tmp/yomux.<login-user>/uploads/<session>/`, where the login user is the authenticated YOmux username rather than the Unix account. Each user tree is private (`0700`), files are removed lazily after seven days by default, and `uploads.retention_days` in `~/.config/yomux/settings.yaml` accepts 1–365 days. Markdown editor image paste uses separate document storage: `/a/b/file.md` creates `/a/b/file/`, writes the image there, and inserts a relative `file/<name>` reference at the captured editor selection.

* Use the pane Info Bar to switch tmux sub-windows (`0:bash`, `1:codex`, ...), cycle among a session's repositories with `< N/M >` or pick one from the `N/M` menu, open transcripts (`Tx`), or inspect the event log (`Log`). A transient tmux-signal patch with no matching window rows retains the populated transcript window strip; only a non-empty signal inventory replaces it, and rejected render records produce typed diagnostics rather than a placeholder.

* File -> `YO!stats` uses the shared `Graphs`, `Cost`, `API/SSE`, `Daemons`, and `Logs` shell when `pane.stats.v1` is available. The browser uses server capabilities and complete snapshots; backend parity gaps are listed in [`GO_MIGRATION.md`](GO_MIGRATION.md).

* One `yomux-go` process owns `stats-v9.sqlite3`, collectors, materialization, routes, and SSE. Storage and migration details are in [`GO_MIGRATION.md`](GO_MIGRATION.md).

* The pane header pop-out button opens supported file previews in a detached browser window. YO!stats opens in a live, single-pane window without YOmux menus or Finder/Differ/Tabber or a minimize control; its controls stay interactive, and its tab leaves the source layout while detached and returns to its prior slot when the window closes.

* File -> `Finder/Differ/Tabber` opens the three independent file-surface tabs. Normal Tabber shows one flat, rounded row per conversation/window: the first line has the shared activity marker, agent symbol, tmux name, and muted activity date; the second line has its description. Normal shows at most one content pane and hides its tab strip. The left Tabber owns select/unselect for conversations, file editors, Finder, Differ, and other tools; unselected items keep their buffers and view state. Select a row to show its content in the pane, select another row to switch content, or select the current row again to hide it. The empty Normal pane does not show the drag-and-drop prompt. Normal and Advanced Tabber default to Ago dates and newest activity first. Existing browsers switch to Ago once after saved browser and URL settings load; later Date/Ago/None choices persist. Rows keep their displayed order while the pointer is anywhere inside Tabber, including pane whitespace. Normal hides the topbar activity counts; Advanced keeps them visible. Activity markers and dates continue updating; the latest order applies after the pointer leaves. Sort and branch controls take effect immediately. Advanced restores the unrestricted pane and drag controls; existing installations keep their previous mode. New Chat opens the existing client and shell choices and assigns the next available numeric tmux name. Right-click offers Rename Description before Rename tmux. Descriptions belong to each window, persist through reloads, restarts, and tmux renames, and a manual description permanently overrides automatic conversation naming. In Advanced, at 900px and wider the available file surfaces live in an explicit narrow left Side Pane; a missing one recreates that Side Pane, and Side tabs never enter Generic Panes. Below 900px there is no Side Pane and File opens only the selected surface in the sole full-width Generic Pane. Widening restores the available file surfaces to the left while leaving YO!\* tabs generic. Finder always annotates the live uncommitted working tree from `HEAD` to the current files, including staged, unstaged, deleted, renamed, and untracked changes; only Differ follows its selectable FROM/TO comparison, so changing those refs does not reload or alter Finder counts. Finder Sync remembers each session's root, manual expansion choices, selection, and touched paths; touched ancestors carry that session's `★`. Sync automatically opens only the selected repository, or the common parent of touched paths when no repository is known, plus the ancestors needed to reach it. It leaves child directories closed even when they contain edited files or the session's working directory. With no touched paths or repository, it uses the working directory. Explicit manual opens and file reveals can open deeper paths. Switching sessions paints the shared bounded cache immediately, then revalidates visible directories in the background. Filesystem permission failures are reported in Finder instead of terminating the request. Finder accepts any accessible absolute root and applies its descriptor identity, bounded-work, role, and protected-root mutation checks. Quick Search is `Mod+P`; it hides clean deleted file tabs, keeps dirty buffers reachable when their backing path is missing, and restores clean tabs when the file reappears.

* In Finder, right-click or long-press one listed Git directory and choose `ΔShow Diff` immediately; its normal Generic Pane tab opens and paints one viewport of metadata-only newest-first commits from a bounded reserve. A separate request then backfills each loaded commit's files and `+N/-N` totals without delaying first paint. Scrolling exposes another viewport from the reserve and obtains another bounded server page when needed. Expand one commit to request that SHA's full message, changed-file tree, status, and per-file `+N/-N` or binary metadata through the consolidated commit-detail route; selecting a file opens another instance of the current Editor/Preview/Diff tab with Diff selected for that commit's exact parent-to-commit refs, immutable Preview content, and read-only Edit. A file row instead offers `Edit in new tab`, `Preview in new tab`, and `ΔShow Diff`; all three activate one canonical working-tree Editor/Preview/Diff tab and select only its mode, so dirty content and pane placement remain intact.

* Finder/Differ uses the native in-process session-files cache and background scheduler to refresh completed results.

* Go Quick Open uses a private SQLite filename index. Directory ancestry is shared across overlapping roots; file contents stay outside the index. The default `index_persist_max_mb` is 1024 MiB (1,073,741,824 bytes) for the combined active database and WAL across all roots, with a 2048 MiB transient ceiling for replacement. Retained rollback files are reported separately. A replacement that cannot fit keeps the prior searchable generation and reports the blocked refresh; a cold root may publish measured partial coverage. Lowering the budget can leave retained data over the new limit until a compliant replacement succeeds. Disabling `index_persist` switches to memory without deleting disk snapshots or rollback files.

* Go loads persisted indexes without immediately crawling every configured root. Explicit searches retain cached results and their refresh ownership when panels hide. Visible scopes use `file_explorer.index_refresh_seconds` (60 seconds by default); hidden roots use `index_background_refresh_seconds` (900 seconds / 15 minutes). Zero disables periodic work for that tier. Searches remain available, and rejected replacements clean up their own unpublished staging files while preserving searchable generations.

* Go applies startup, GET, POST, external YAML edits, and retry through one settings owner. Budget, file-limit, and refresh-cadence changes keep compatible results searchable and request reconciliation. Root or exclusion changes fence affected results until acknowledged. Malformed YAML retains the last valid configuration. Settings and index status expose configured, active, and snapshot revisions as decimal strings, worker incarnation, synchronization errors, coverage, and database/WAL/staging/rollback byte counts. Discovery, repair, and fallback search use the same bounded descriptor-based breadth-first traversal and exclusions. Bare Git repositories, dependency/cache/secret/VCS/build directories, object and library files, bytecode, and native executables are excluded. Scripts, source, images, PDFs, archives, and generic `.bin` assets remain eligible; normal Finder listings remain available.

* Tabber lists open tabs and tmux sub-windows by recent activity. `Mod+B` hides Finder/Differ/Tabber or restores the default left Side Pane on wide layouts. The top-bar language picker changes the live UI.

* Native YO!agent provides conversations, skills, jobs, session watches, and reviewed sends. Responses use the installed Claude or Codex CLI. Model-proposed sends require confirmation. See [`docs/YOAGENT.md`](docs/YOAGENT.md) for configuration and send rules.

* File -> `YO!chat` opens the native shared conversation for authenticated users within the selected local deployment. It provides persisted history, search, typing presence, and `/yo` questions through YO!agent. The server enforces the configured retention policy. Cross-host room sharing is unsupported.

* Cross-pane notifications appear in one global toast rail and identify their target tab without changing your current focus. Attention remains until acknowledged; completion, chat, PR, and job notices are coalesced by target. Clicking a notice opens its target and clears it. Uploads and file/editor errors remain in the pane where that direct action occurred. Preferences independently control in-YOmux and system notifications.

* Tab attention badges surface agents waiting for input or approval even when automatic approval is off. YOmux tracks one canonical Claude/Codex identity per physical tmux pane, so short-lived searches or tests that mention an agent name cannot create duplicate status rows or finished notifications. Visible spinner/timer history is bounded and resets when it disappears, so a reused tmux pane cannot inherit stale working state. Unknown footer chrome does not cancel a visible Working row, while a real later completion, shell prompt, choice, or AskUserQuestion does; a current question also outranks a stale `Goal blocked` footer.

* The browser title, favicon badge, and topbar activity count report working Claude/Codex sub-windows, so two active agents inside one tmux session count as two everywhere.

* Touch/coarse-pointer terminals provide a movable fixed-size smart-key palette whose launcher shares inactive-tab paint. Opening it hides every launcher copy and shows the primary page; More swaps to a bounded second page without resizing or internal scrolling, the full-width top handle moves the palette and launcher together, and the top-right X closes it and restores every launcher at the remembered position. Esc and Ctrl/Shift/Alt sit left of the cursor pad, shared Ctrl-C stays bottom-right on both pages, and PgUp/PgDn retain wider targets. A plain terminal tap focuses xterm's native input and opens the device keyboard; text, composition/IME, Backspace, Return, and paste use the same terminal transport as desktop input. A vertical finger pan scrolls tmux history without opening the software keyboard, while alternate-screen apps with active mouse reporting receive line-accurate wheel input and other alternate-screen apps retain cursor-aware arrows. In Codex, ordinary wheel or trackpad scrolling moves through the conversation while keeping the prompt fixed. Page Up/Down (Fn+Up/Down on Mac) sends native application keys. Hold Shift while scrolling, or use Shift+Page Up/Down, to browse tmux history; Esc exits tmux copy mode.

* Phone layouts show the selected tab in one full-width pane while preserving the restored desktop split for wider viewports. Editor, Preview, and Diff actions use touch-sized versions of the existing controls; Markdown image paste/upload and pane switching stay on the same desktop action paths. Once a finger claims Preview scrolling, deferred render, zoom, split-reflection, and layout restoration work yields to that live position instead of snapping backward.

* For a real-device mobile diagnostic, open YOmux with `?debug=1`, reproduce the Preview or terminal input problem once, then open `YO!stats` -> `API/SSE` and press `Copy Events`. That debug-only action copies raw JSON containing the Preview touch/viewport/write owners and generations plus terminal tap/focus/input stages; normal sessions do not retain this mobile trace.

For durable UI rules, see [`docs/specs/GUI.md`](docs/specs/GUI.md); current browser tests own exact edge cases and assertions.

### Copying terminal text

* Select text and press `Cmd-C` (Mac) / `Ctrl-C` (PC) to copy it to your browser clipboard. While a full-screen app like Claude owns the mouse, a normal drag goes to the app instead of making a selection — hold `Option` (Mac) / `Shift` (PC) and drag to force a real terminal selection, or just select inside the app: its own copy (sent as an OSC 52 escape) is forwarded to your browser clipboard automatically (the status line shows `copied N chars`).

* `Cmd-C` with nothing selected does nothing — it is never delivered to the running program. Plain `Ctrl-C` with nothing selected still sends `SIGINT` to interrupt the program.

* To copy the tmux copy-mode selection (server-side, via tmux), press `Cmd-Option-C` (Mac) / `Ctrl-Alt-C` (PC), or right-click and choose `Copy tmux selection`.

* Right-click keeps the current selection highlighted and offers `Copy` / `Copy without indent`. When Claude owns the visible highlighted block and sends it through OSC 52, the right-click menu must preserve that app-side block; it must not re-read and copy only the small text under the cursor.

* Right-clicking a URL in a terminal pane or rendered markdown puts `Open URL in a new tab` first, then `Copy URL`; when the visible selected text differs from the actual href, the menu labels that path explicitly as `Copy selected text`.

* After a terminal copy/open action consumes selected text, YOmux clears stale browser/xterm selection. Explicit `Copy tmux selection` also exits tmux copy-mode after copying so selected rows do not stay painted as green blocks.

The `YO` button controls existing-session auto-approval. Go owns per-pane process identity, shared terminal-writer locks, guarded prompt delivery, configured rules, dry-run behavior, and repeated recovery for the current Codex capacity notice. The `--dangerously-yolo` launch flag remains separate and controls permission flags for newly launched agents. See [`GO_MIGRATION.md`](GO_MIGRATION.md) and [Agent permissions & YOLO](#agent-permissions--yolo).

## Retired Python behavior

The Python server, `--sessions`, transcript/runtime report modes, the Python development reloader, optional login decoys, firewall escalation, and unavailable integrations are retired. Unsupported API routes return typed unavailable responses. [Launch options](#launch-options) lists the supported native selectors.

Search & Runs event search and compact run history, background AI activity summaries, watched pull requests, and hosted GitHub/Linear enrichment in YO!info are retired Python-only behavior. A remaining browser panel or saved layout does not supply those services. Native terminal transcripts, per-session event logs, local Finder/Git views, chat, and YO!agent conversations remain separate supported features.

## Authentication & roles

The Go server applies the route catalog's role policy before handling authenticated requests. Browser login, Basic authentication, and terminal WebSocket authentication share credential admission.

| Role | Can do |
| --- | --- |
| `admin` | Type into tmux panes, create sessions, upload files, toggle `YO`, switch tmux sub-windows, and configure native YO!agent. |
| `readonly` | View panes, transcripts, branch metadata, logs, YOLO status, and Repo Diff history, statistics, changed-file lists, and file comparisons. Terminals and Repo Diff file views are read-only. |

Read-only access is not a filesystem sandbox. Go admits accessible filesystem reads and searches according to its route policy; writing files, uploading, and sending terminal input require `admin` access.

Browser writes, login submissions, logout, and terminal connections reject another scheme, host, or port. Normal page navigation and command-line requests without browser-origin headers remain supported. Terminating a session requires `POST /api/kill-session`.

Login cookies have a 90-day lifetime and use a port-scoped name backed by the configuration directory's secret. Changing an account password changes its cookie signature input and invalidates existing cookies. Go credential admission covers form, Basic, and WebSocket authentication; unavailable admission storage fails closed before password hashing. Python recovery commands, throttle diagnostics, login decoys, and firewall escalation are retired.

## Agent permissions & YOLO

Go supports `--dang` for new sessions and `yolo.auto-approve.v1` for existing-session YO. YO!agent has native Go handlers for conversations, skills, jobs, watches, and reviewed sends. Go's File -> New menu launches bash, Claude, and Codex with the corresponding dangerous flags when that opt-in is enabled.

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
yomux --port 9998 --dang
```

With `--dang`, `+ Claude` launches `claude --dangerously-skip-permissions`, so permission prompts are bypassed for new Claude sessions (hooks and OAuth login are left intact — see the note above on why `--bare` is not used). `+ Codex` launches `codex --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust`, so both command approval/sandbox checks and hook trust checks are bypassed for new Codex sessions.

Without it, those buttons create plain `claude` / `codex` sessions. This flag does not change existing sessions.

**The `YO` toggle.** Per-session auto-approval for an existing tmux session. It watches the visible tmux screen and sends the approval key when the rule engine says the prompt is safe. Rules live in `~/.config/yomux/yolo-rules.yaml`:

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

The managed Go launcher binds `--host 0.0.0.0` (all interfaces) by default: YOmux supports reaching sessions from another machine on a trusted LAN, and every product request is gated by login. The Go binary itself defaults to `127.0.0.1` when run directly. If you use the managed all-interface launch, restrict the port to trusted IPs at the firewall:

Restrict remote access with a firewall or tunnel. The server does not manage firewall rules; the old optional login escalation and recovery CLI are retired.

```bash
sudo ufw allow from <client-ip> to any port 9998 proto tcp
```

To keep YOmux local-only instead, bind loopback and tunnel from your client:

```bash
yomux --host 127.0.0.1 --port 9998 --dang
autossh -M 0 -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 9998:127.0.0.1:9998 user@server
```

## Optional developer tools

Python prompt-analysis helpers, tmux-wall diagnostics, mocks, fixture builders, and pytest controllers remain source-only developer tools. They are outside the installed product runtime. Use the native browser's YO controls for supported auto-approval and its terminal panels for session monitoring.

## License

YOmux is licensed under PolyForm Noncommercial 1.0.0. Noncommercial use is allowed under that license. Commercial use requires a separate commercial license from Keiven Chang.

Third-party code and generated dependency bundles keep their own upstream notices; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
