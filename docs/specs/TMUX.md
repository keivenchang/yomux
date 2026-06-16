# TMUX signals available to YOLOmux

Reference for every signal tmux exposes, captured from this host's `man tmux` (tmux 3.4). "Signals" fall into three kinds: **state** (FORMATS variables you query on demand), **events** (hooks and control-mode notifications you subscribe to), and **content** (pane buffers you read). All are read-only ways to observe tmux without attaching a sizing client — relevant to the terminal resize/minimize work (see `DOIT.75`, `DOIT.76`).

## How to read signals

- Query any format on any target: `tmux display-message -p -t <target> '#{var}'` (or `-F` on `list-*`).
- Enumerate: `tmux list-sessions -F …`, `list-windows -a -F …`, `list-panes -a -F …`, `list-clients -F …`, `list-buffers -F …`.
- Options/state: `tmux show-options -s|-g|-w|-p`, `show-hooks -g`, `show-environment`.
- Content (no attach, no resize): `tmux capture-pane -t <target> -p [-J] [-S -N]`, or stream raw output with `tmux pipe-pane -t <target> -o 'cat >> file'`.
- Real-time event stream: `tmux -C attach` / `tmux -C new` (control mode) emits `%…` notifications; or run commands on hooks via `set-hook`.
- Target spec: `session:window.pane` (e.g. `1:0`, `1:1.2`), plus `#{pane_id}` (`%N`), `#{window_id}` (`@N`), `#{session_id}` (`$N`).

`display-message -p` evaluates expressions too: conditionals `#{?cond,a,b}`, comparisons `#{e|…}`, matching `#{m:…}`, content search `#{C/r:…}`, loops `#{S:…}`/`#{W:…}`/`#{P:…}`/`#{T:…}`, time `#{t:…}`, length `#{n:…}`, padding/trim `#{p…}`/`#{=…}`.

---

## State signals — FORMATS variables (187)

Aliases shown where they exist (e.g. `#S`).

### Server (8)

| variable | meaning |
|---|---|
| pid | Server PID |
| socket_path | Server socket path |
| start_time | Server start time |
| uid | Server UID |
| user | Server user |
| version | Server version |
| server_sessions | Number of sessions |
| next_session_id | Unique session ID for next new session |

### Host (2)

| variable | meaning |
|---|---|
| host (`#H`) | Hostname of local host |
| host_short (`#h`) | Hostname, no domain |

### Session (24)

| variable | meaning |
|---|---|
| session_id | Unique session ID (`$N`) |
| session_name (`#S`) | Name of session |
| session_path | Working directory of session |
| session_created | Time session created |
| session_activity | Time of session last activity |
| session_last_attached | Time session last attached |
| session_attached | Number of clients session is attached to |
| session_attached_list | List of clients session is attached to |
| session_many_attached | 1 if multiple clients attached |
| session_windows | Number of windows in session |
| session_stack | Window indexes in most recent order |
| session_alerts | List of window indexes with alerts |
| session_marked | 1 if session contains the marked pane |
| session_format | 1 if format is for a session |
| session_group | Name of session group |
| session_grouped | 1 if session in a group |
| session_group_size | Size of session group |
| session_group_list | List of sessions in group |
| session_group_attached | Number of clients sessions in group are attached to |
| session_group_attached_list | List of clients sessions in group are attached to |
| session_group_many_attached | 1 if multiple clients attached to sessions in group |
| active_window_index | Index of active window in session |
| last_window_index | Index of last window in session |

### Window (35)

| variable | meaning |
|---|---|
| window_id | Unique window ID (`@N`) |
| window_index (`#I`) | Index of window |
| window_name (`#W`) | Name of window |
| window_active | 1 if window active |
| window_activity | Time of window last activity |
| window_activity_flag | 1 if window has activity (needs monitor-activity) |
| window_bell_flag | 1 if window has bell |
| window_silence_flag | 1 if window has silence alert |
| window_active_clients | Number of clients viewing this window |
| window_active_clients_list | List of clients viewing this window |
| window_active_sessions | Number of sessions on which this window is active |
| window_active_sessions_list | List of sessions on which this window is active |
| window_linked | 1 if window is linked across sessions |
| window_linked_sessions | Number of sessions this window is linked to |
| window_linked_sessions_list | List of sessions this window is linked to |
| window_panes | Number of panes in window |
| window_width | Width of window |
| window_height | Height of window |
| window_cell_width | Width of each cell in pixels |
| window_cell_height | Height of each cell in pixels |
| window_layout | Layout description, ignoring zoomed panes |
| window_visible_layout | Layout description, respecting zoomed panes |
| window_flags (`#F`) | Window flags (`#` escaped as `##`) |
| window_raw_flags | Window flags, nothing escaped |
| window_zoomed_flag | 1 if window is zoomed |
| window_bigger | 1 if window is larger than client |
| window_offset_x | X offset into window if larger than client |
| window_offset_y | Y offset into window if larger than client |
| window_stack_index | Index in session most recent stack |
| window_start_flag | 1 if window has the lowest index |
| window_end_flag | 1 if window has the highest index |
| window_last_flag | 1 if window is the last used |
| window_marked_flag | 1 if window contains the marked pane |
| window_format | 1 if format is for a window |

### Pane — geometry, process, state (≈55)

| variable | meaning |
|---|---|
| pane_id (`#D`) | Unique pane ID (`%N`) |
| pane_index (`#P`) | Index of pane |
| pane_active | 1 if active pane |
| pane_last | 1 if last pane |
| pane_marked | 1 if this is the marked pane |
| pane_marked_set | 1 if a marked pane is set |
| pane_width | Width of pane |
| pane_height | Height of pane |
| pane_left / pane_right / pane_top / pane_bottom | Pane edges |
| pane_at_left / _right / _top / _bottom | 1 if pane is at that window edge |
| pane_title (`#T`) | Title of pane (app-settable) |
| pane_current_command | Current command if available |
| pane_current_path | Current path if available |
| pane_start_command | Command pane started with |
| pane_start_path | Path pane started with |
| pane_path | Path of pane (app-settable) |
| pane_pid | PID of first process in pane |
| pane_tty | Pseudo terminal of pane |
| pane_bg / pane_fg | Pane background / foreground colour |
| pane_in_mode | 1 if pane is in a mode |
| pane_mode | Name of pane mode, if any |
| pane_synchronized | 1 if pane is synchronized |
| pane_input_off | 1 if input to pane is disabled |
| pane_pipe | 1 if pane is being piped |
| pane_dead | 1 if pane is dead |
| pane_dead_signal | Exit signal of process in dead pane |
| pane_dead_status | Exit status of process in dead pane |
| pane_dead_time | Exit time of process in dead pane |
| pane_unseen_changes | 1 if there were changes in pane while in mode |
| pane_tabs | Pane tab positions |
| pane_format | 1 if format is for a pane |
| cursor_x / cursor_y | Cursor position in pane |
| cursor_character | Character at cursor in pane |
| cursor_flag | Pane cursor flag |
| scroll_region_upper / scroll_region_lower | Top / bottom of scroll region |
| alternate_on | 1 if pane is in alternate screen |
| alternate_saved_x / alternate_saved_y | Saved cursor in alternate screen |
| insert_flag | Pane insert flag |
| keypad_flag / keypad_cursor_flag | Pane keypad flags |
| origin_flag | Pane origin flag |
| wrap_flag | Pane wrap flag |
| history_size | Size of history in lines |
| history_limit | Maximum window history lines |
| history_bytes | Number of bytes in window history |

### Client (28)

| variable | meaning |
|---|---|
| client_name | Name of client |
| client_tty | Pseudo terminal of client |
| client_pid | PID of client process |
| client_uid / client_user | UID / user of client process |
| client_session | Name of the client's session |
| client_last_session | Name of the client's last session |
| client_created | Time client created |
| client_activity | Time client last had activity |
| client_width / client_height | Size of client |
| client_cell_width / client_cell_height | Cell size in pixels |
| client_flags | List of client flags (e.g. attached, focused, active-pane, read-only, ignore-size) |
| client_prefix | 1 if prefix key has been pressed |
| client_readonly | 1 if client is read-only |
| client_control_mode | 1 if client is in control mode |
| client_key_table | Current key table |
| client_termname / client_termtype | Terminal name / type |
| client_termfeatures | Terminal features, if any |
| client_utf8 | 1 if client supports UTF-8 |
| client_written | Bytes written to client |
| client_discarded | Bytes discarded when client behind |

### Buffer / paste (4)

| variable | meaning |
|---|---|
| buffer_name | Name of buffer |
| buffer_size | Size in bytes |
| buffer_created | Time buffer created |
| buffer_sample | Sample of start of buffer |

### Copy-mode / selection / search (16)

| variable | meaning |
|---|---|
| copy_cursor_x / copy_cursor_y | Cursor position in copy mode |
| copy_cursor_line | Line the cursor is on in copy mode |
| copy_cursor_word | Word under cursor in copy mode |
| scroll_position | Scroll position in copy mode |
| rectangle_toggle | 1 if rectangle selection is active |
| selection_present | 1 if selection started in copy mode |
| selection_active | 1 if selection started and tracks the cursor |
| selection_start_x / selection_start_y | Start of selection |
| selection_end_x / selection_end_y | End of selection |
| search_present | 1 if search started in copy mode |
| search_match | Search match if any |
| pane_search_string | Last search string in copy mode |

### Mouse (15)

| variable | meaning |
|---|---|
| mouse_x / mouse_y | Mouse position, if any |
| mouse_line | Line under mouse, if any |
| mouse_word | Word under mouse, if any |
| mouse_hyperlink | Hyperlink under mouse, if any |
| mouse_status_line | Status line on which mouse event took place |
| mouse_status_range | Range type/argument of mouse event on status line |
| mouse_standard_flag / mouse_button_flag / mouse_any_flag / mouse_all_flag / mouse_sgr_flag / mouse_utf8_flag | Pane mouse mode flags |

### Hook context (7) — only set while a hook runs

| variable | meaning |
|---|---|
| hook | Name of running hook, if any |
| hook_client | Name of client where hook was run |
| hook_session / hook_session_name | ID / name of session where hook ran |
| hook_window / hook_window_name | ID / name of window where hook ran |
| hook_pane | ID of pane where hook ran |

### Command listing / config / misc (8)

| variable | meaning |
|---|---|
| command | Name of command in use, if any |
| command_list_name / command_list_alias / command_list_usage | Set while listing commands |
| config_files | List of configuration files loaded |
| current_file | Current configuration file |
| line | Line number in the list |

---

## Event signals — hooks (22 named + after-`<command>`)

Subscribe with `set-hook [-g] <hook-name> '<tmux command>'`; list with `show-hooks -g`. Most commands also fire an `after-<command>` hook (e.g. `after-split-window`, `after-resize-pane`). The hook-context `hook_*` formats above are available inside the command.

| hook | fires when |
|---|---|
| client-active | a client becomes the latest active client of its session |
| client-attached | a client is attached |
| client-detached | a client is detached |
| client-resized | a client is resized |
| client-focus-in / client-focus-out | focus enters / exits a client |
| client-session-changed | a client's attached session changes |
| window-linked / window-unlinked | a window is linked into / unlinked from a session |
| window-renamed | a window is renamed |
| window-resized | a window is resized (may be after client-resized) |
| session-created / session-closed / session-renamed | session lifecycle |
| pane-exited | the program in a pane exits |
| pane-died | the program exits but remain-on-exit keeps the pane |
| pane-focus-in / pane-focus-out | focus enters / exits a pane (needs focus-events) |
| pane-set-clipboard | terminal clipboard set via the xterm OSC 52 escape |
| alert-activity / alert-bell / alert-silence | window activity / bell / silence (see monitor-activity, monitor-bell, monitor-silence) |

## Event signals — control mode (`tmux -C`) notifications

A real-time line-based stream — observe events with no polling and no sizing client.

`%output`, `%extended-output`, `%begin` / `%end` / `%error`, `%exit`, `%continue` / `%pause`, `%message`, `%config-error`, `%session-changed`, `%session-renamed`, `%sessions-changed`, `%session-window-changed`, `%client-detached`, `%client-session-changed`, `%window-add`, `%window-close`, `%window-renamed`, `%window-pane-changed`, `%unlinked-window-add`, `%unlinked-window-close`, `%unlinked-window-renamed`, `%layout-change`, `%pane-mode-changed`, `%paste-buffer-changed`, `%paste-buffer-deleted`, `%subscription-changed`.

`refresh-client -B name:format` (control mode) creates a **subscription** that re-emits `%subscription-changed` whenever a format's value changes — push-based formats without polling.

## Content signals — reading a pane without attaching

- `capture-pane -t <target> -p` prints the pane's current screen (`-J` joins wrapped lines, `-S -N` / `-E` include scrollback, `-e` keeps escape sequences). Works on any pane, active or not; does **not** attach a client or resize.
- `pipe-pane -t <target> -o '<cmd>'` streams the pane's raw output to a command as it is written — live content from an inactive window with no sizing client.
- Caveat: both read what the program drew at the window's **current** size; they reflect a shrunk window but cannot enlarge it.

---

## YOLOmux application notes (resize / minimize / authority)

Relevant to `DOIT.75` (terminals periodically "minimize" because tmux `window-size latest` lets a small/inactive client size the shared window) and the capture-based features (auto-approve, transcript, summary).

- **Who is actually looking:** `window_active_clients` / `window_active_clients_list` tell you how many (and which) clients view a window. A window with `window_active_clients == 0` has no viewer, so its size matters to nobody. YOLOmux now includes parsed `list-clients` rows in the signal snapshot and annotates each window with active client details.
- **Which client is foreground:** `client_activity` (per client) is the recency tmux's `latest` policy keys off; the max-`client_activity` viewing client is the de-facto foreground. `client_flags` includes `focused`/`active-pane`. Combined with each client's `client_width`/`client_height`, the server marks an `authoritative_client` for the window from the most-recent active non-control viewer and ignores stale/non-viewing/control-mode clients.
- **Output recency:** `window_activity` (epoch) / `window_activity_flag` show how recently a window produced output.
- **Server-wide activity state:** YOLOmux reads `list-windows -a`, `list-panes -a`, and `list-clients` into one cached tmux signal snapshot, publishes it over `/api/tmux-signals` and the client-event stream, and uses one read-only control-mode client plus scoped hooks to invalidate that snapshot on tmux activity instead of depending only on blind polling.
- **Idle capture gate:** auto-approve and live session status consult the snapshot before `capture-pane`; if the target window is older than the tmux activity window and no worker is tracking a pending prompt hash, YOLOmux skips that capture tick and reports the pane as idle. Known pending prompts always bypass the gate until a later capture proves the prompt cleared.
- **All agent panes:** a session-level YOLO enable remains the stored user setting, but the server fans that setting out to every live agent pane target discovered in the tmux snapshot for that session. This lets background tmux windows get approval detection even when the browser is not displaying that window.
- **Agent signal UI:** the browser derives tab state and YO!agent recent-agent chips from the same snapshot. Dead agent panes render `DONE` with the tmux exit status and offer a same-agent new-session restart action; `pane_current_command` plus `alternate_on`/`pane_pid` can mark a running Claude/Codex agent even without transcript activity; `window_silence_flag` can mark a quiet agent as done; `window_bell_flag` maps to the existing attention/notification path; `pane_in_mode`, `pane_mode`, `pane_input_off`, and `pane_synchronized` render compact mode/read-only/sync chips.
- **Recency:** YO!agent Recent Agents prefers `window_activity` for the row's relative activity text and row ordering when tmux provides it, falling back to transcript timestamps otherwise. Rows whose tmux activity is outside the active window are dimmed.
- **Presence:** YO!agent Recent Agents shows active-client presence chips from `window_active_clients` / `window_active_clients_list` / parsed client rows. This complements the existing YO!share viewer list, which remains the source for share-token guest browser details.
- **Finder context:** Finder sync and recent-agent path display prefer direct `pane_current_path` from the tmux snapshot when present, then fall back to transcript metadata. This avoids stale transcript-derived roots after a shell `cd`.
- **Scrollback-aware snapshots:** `/api/tmux-snapshot` uses `history_size` / `history_bytes` to cap the `capture-pane -S` depth to the available history and returns an `unchanged` response when the target pane history signature has not grown since the last capture. Approval detection still captures live visible content and does not use this skip path.
- **Zoom/layout:** YO!agent Recent Agents surfaces `window_zoomed_flag` as a chip and keeps `window_layout` / `window_visible_layout` in row titles; `%layout-change` and the `window-resized` hooks invalidate the same snapshot.
- **Push-based formats:** the read-only control-mode client subscribes with `refresh-client -B` for window activity and layout formats so `%subscription-changed` can wake the same snapshot path without relying only on the periodic poll.
- **Size facts:** `client_width/height`, `window_width/height`, `pane_width/height`, `*_cell_width/height`, `window_bigger`, `window_offset_x/y`.
- **Events instead of polling:** hook `client-active` / `client-resized` / `window-resized` / `client-detached`, control-mode `%layout-change` / `%client-detached`, and `%subscription-changed` drive re-evaluation of the authoritative size and UI signal state.
- **Read without resizing:** `capture-pane` / `pipe-pane` read inactive panes with no sizing side effect; only `attach-session` (the live interactive terminal) creates a client that participates in window sizing.

Verify policy live with: `tmux show-options -g window-size` (here `latest`), `tmux show-window-options -g aggressive-resize` (here `off`), `tmux list-clients -F '#{client_session} #{client_width}x#{client_height} #{client_activity}'`, and `tmux list-windows -a -F '#{window_index} #{window_active_clients} #{window_activity}'`.
