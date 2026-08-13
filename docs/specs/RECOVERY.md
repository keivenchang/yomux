# Recovery and Lost Sessions

How YOLOmux decides which tmux sessions are worth saving, how it classifies a session's disappearance as clean or unclean, and what it may honestly claim to cover.

Two constraints here exist nowhere else in the repo and are the reason this document does. First, the clean-versus-unclean classification table below: an interactive user can deliberately exit carrying the previous command's nonzero status, so exit status is not intent, and an unknown close is dismissed as intentional rather than silently deleted. Second, the discoverability boundary: a custom tmux socket created and destroyed before YOLOmux observed it cannot be reconstructed by any later process, so the UI and docs must say so rather than claim impossible coverage.

Field-level tmux signals are in [TMUX.md](TMUX.md); the Lost Tabber surface is in [GUI.md](GUI.md).

## Recovery Product Plan

YOLOmux does not own the set of sessions worth saving. Every live session it can observe on an owner-verified known, configured, registered, or discovered tmux socket is tracked automatically, including sessions created directly by a person or indirectly by Claude/Codex skills. The minimum baseline is socket/session/window/pane identity, incarnation, cwd, current command/shell, pane PID, topology, and observation timestamps. Provider, transcript, and exact resume argv are enrichment fields: absence of those fields limits recovery to Shell only but never excludes the session from loss history.

“All” has a discoverability boundary. The default socket, configured sockets, durable registry entries, and platform-discoverable same-user tmux server sockets can be inventoried automatically. A skill that creates a custom `tmux -S` socket must register it. If a custom socket and all its sessions are created and destroyed before YOLOmux or a persistent tmux hook observes them, no later process can reconstruct their names, cwd, topology, or exit reason; the UI and docs must state this limitation instead of claiming impossible coverage.

### Clean versus unclean evidence

The local tmux 3.5a probes establish the classification mechanism:

| Probe | tmux evidence | Classification |
| --- | --- | --- |
| Pane command exits 0 with normal tmux behavior | `pane-exited`, then `session-closed`; the removed pane no longer exposes status/signal | Clean only when the lifecycle records match the previously observed pane/incarnation; discard from Lost history after a short deduplication tombstone. |
| `remain-on-exit failed`, pane exits 7 | `pane-died`, `pane_dead=1`, `pane_dead_status=7` | Unclean; journal status 7 and show red. |
| `remain-on-exit failed`, pane receives SIGTERM | `pane-died`, `pane_dead=1`, `pane_dead_signal=15` | Unclean; journal signal 15 and show red. |
| External `tmux kill-session` | `session-closed` without matching `pane-exited`/`pane-died` evidence | Unknown/force kill; show red until dismissed. |
| tmux server crash, SIGKILL, OOM, or host outage | Socket/server disappears before close hooks can run | Unexpected; show red with the strongest independent cause evidence. |

Install lifecycle hooks idempotently and append them to hook arrays; never overwrite existing user hooks. Preserve a user's explicit `remain-on-exit on` behavior. Where the option is off and tmux supports it, set `remain-on-exit failed` so nonzero/signal deaths remain inspectable long enough to journal their status/signal, then apply a documented cleanup/repair action. Exit status is not perfect intent—an interactive user can deliberately exit with the previous command's nonzero status—so the safe policy is to show that red row with `Dismiss as intentional` rather than silently delete recoverable state. On unsupported tmux versions or missing hook evidence, classify as unknown, not clean.

Implementation must proceed in dependency order. Do not polish the Lost Sessions UI on top of ambiguous session identity or stale server state.

1. Establish one typed target and incarnation model across socket discovery, tmux inventory, rename, journal records, APIs, browser keys, and terminal attachment. A GUI rename must migrate the durable recipe in the same operation; reusing a name must create a new incarnation rather than attach history to the new process.
2. Add owner-verified socket discovery/registration and automatic baseline adoption for every observed live session. Install idempotent lifecycle hooks and reconcile hook events with periodic inventory before implementing loss UI.
3. Establish the shared journal transaction model and multi-server revision/claim behavior. Intentional close and clean-exit evidence must be durable before history is discarded, while a nonzero/signal exit, external kill, server crash, or outage must remain recoverable after the process that observed it is gone.
4. Record exact launch recipes and provider transcript identity before YOLOmux-created sessions, then reconcile the pending recipe with the actual tmux pane. Enrich externally created sessions from safe process/transcript evidence without making provider metadata a prerequisite for baseline tracking. Preserve argv tokens and their order; do not reconstruct flags from current Preferences.
5. Launch Claude and Codex as children of an interactive shell and prove every agent exit path returns to that shell. Keep Term as a plain shell and preserve the final shell exit evidence separately.
6. Implement recovery mutations through one server transaction that owns tmux creation, journal transition, session-registry reconciliation, and the response revision. Do not make each endpoint invent a separate refresh path.
7. Render live and Lost Tabber rows from one selector. The selector owns newest-incarnation choice, incident grouping, pane deduplication, counts, Recover All membership, exit evidence, and conflict state.
8. Add the preflight sheet and recovery actions only after the backend can return an authoritative action plan and revision.
9. Add event-driven client convergence. Successful recovery must remove the Lost row and add the live target in one render pass; polling remains a fallback for events that happen outside YOLOmux.
10. Add focused unit/contract tests, then the isolated browser E2E matrix below. Fix every failure at the shared owner, repeat the exact failed journey, run the whole matrix, and only then run the canonical gate.
11. Update user/operator docs and working skills only after the tested behavior is settled, so docs do not describe an aspirational path as shipped.

## Recovery Preflight Sheet

The preflight sheet is the last review barrier before YOLOmux creates or attaches a process. It is not a generic confirmation dialog. It is a revision-bound snapshot of what the server will do, and the action must be rejected and re-preflighted if the journal or tmux target changed while the sheet was open.

The sheet must show the exact source incident and incarnation, target socket and session name, whether that target is free or already live, saved windows/panes/layout, cwd and repository drift, shell, provider, full transcript ID, exact original argv tokens in order, exact continuation argv, and a plain statement of what will and will not be recovered. It must say that unsaved terminal input, shell variables, process memory, in-progress tool calls, and open sockets cannot be restored.

Each action has distinct semantics:

| Action | Required behavior |
| --- | --- |
| `Recover` | Use the original free name, restore the saved shell/layout, and resume only panes with a uniquely attributable provider transcript and valid exact argv. |
| `Shell only` | Restore the shell/layout and cwd without launching any provider. It must work for Term records and missing/unsupported provider records. |
| `Restore as new name` | Validate the new name and apply the currently selected recovery mode. For Term or missing-provider records it is necessarily shell-only; it must never send an AI-resume request. |
| `Attach existing` | Attach the browser to the already-live target after identity checks. It must not create, kill, rename, or claim another session. |
| `Retry` | Re-run preflight against current state and issue a new idempotent claim only after the user confirms the updated plan. |
| `Copy transcript ID` / `Copy command` | Copy the exact displayed value and make no server or tmux mutation. |
| `Dismiss as intentional` | Resolve only that incident/history record. It must not kill, attach, rename, or change a currently live tmux target. |
| `Cancel` | Close the sheet with no journal, server, tmux, or browser-layout mutation. |

The primary button must name the action (`Recover`, `Restore shell`, or `Restore as new name`), not say `Continue?`. Disabled actions must retain a readable explanation. The sheet must not show duplicate panes, duplicate incidents, raw secrets, inherited environment, or an enabled AI continuation action when provider/transcript evidence is unavailable.

## Isolated End-To-End Environment

Recovery QA must never kill sessions on the default socket or use live ports 7770-7773/8880-8883. Each run owns an isolated `/tmp` root, private tmux socket, isolated `YOLOMUX_STATE_DIR`, isolated config/cache/upload directories, ephemeral HTTP ports, unique session names, and cleanup that targets only those recorded resources. Two YOLOmux server processes must share the isolated state and tmux socket so cross-server classification and UI convergence are exercised.

The browser lane uses real Chrome/Selenium against the generated bundle served by the restarted isolated process. Before the first action, record both server PIDs, `/proc/<pid>/cwd` or platform equivalent, bundle hash/build revision, state directory, socket path, auth mode, and a hard-reload marker. Fail immediately if another process owns either port, the server cwd is not this checkout, the served bundle does not match the built bundle, or the browser reports a JavaScript error/unhandled rejection.

Provider verification has two layers. A deterministic provider-recorder executable captures exact argv token order, cwd, environment allowlist, exit behavior, and requested transcript ID without making external calls; it drives the full matrix cheaply. Required release smoke then creates disposable real Claude and Codex transcripts, launches each through the UI with safe and dangerous modes, force-kills the isolated tmux server, recovers from the browser, and proves the real client continued the same transcript. Credentials and transcript contents must never be copied into evidence; only provider, transcript ID, redacted argv, and observable continuation identity are retained.

The fixture may read the recovery journal and tmux inventory for assertions, but it must never insert, rename, resolve, or otherwise edit journal records directly. Loss, rename, intent, conflict, retry, and recovery must occur through the same UI/API/tmux event paths used by a person. A simulated boot-ID change covers deterministic automation, but one opt-in operator run through a real host reboot is still required before claiming host-outage recovery complete.

## End-To-End Scenario Matrix

Every row is required unless it explicitly says operator-only. Each row must prove the browser state, journal state, tmux/process state, and recovered terminal behavior together.

| ID | Setup and action | Required observable result |
| --- | --- | --- |
| E01 | Create Term from File menu. | One shell-backed tmux target and one recipe; no provider/transcript; an observed exit-zero shell closure removes the live row and creates no Lost row on either server. |
| E02 | Create Claude in safe mode with a disposable transcript. | Agent runs as a child of the saved shell; Tabber shows the pane-bound transcript ID and exact safe argv; normal agent exit returns to a usable shell with no Lost row. |
| E03 | Create Claude with `--dangerously-skip-permissions` and any selected model flag. | Preflight, copy command, journal, process argv, and resumed process preserve every original token and order; no flag is dropped or replaced. |
| E04 | Create Codex in safe mode with a disposable transcript. | Agent runs as a child of the saved shell; Tabber shows the pane-bound transcript ID and exact safe argv; normal agent exit returns to a usable shell with no Lost row. |
| E05 | Create Codex with `--dangerously-bypass-approvals-and-sandbox`, `--dangerously-bypass-hook-trust`, and a selected profile/model flag. | Preflight, copy command, journal, process argv, and resumed process preserve every original token and provider-specific `resume` placement. |
| E06 | Exit Claude/Codex normally, exit nonzero, send Ctrl-C, then SIGKILL only the agent child. | The supervising shell remains the same usable tmux pane after every case; the live row remains and no Lost row appears. |
| E07 | Kill a session through File/right-click on server A while server B and two browsers observe. | Intent is durable before tmux disappearance; both browsers remove the live row without ever displaying red; no recoverable incident is created. |
| E08 | Restart one YOLOmux web server and disconnect/reconnect a browser while tmux remains live. | No process is relaunched, no Lost record appears, and both servers converge on the same live rows. |
| E09 | Rename `1 -> killme` through the GUI, then force-loss it. | Journal identity, live Tabber label, tmux name, pane metadata, and recovery target all use `killme`; no stale `1` Lost row appears. |
| E10 | After E09, create a new `1`, then later create/reuse `killme` in a separate incarnation. | Each live process has a distinct incarnation; history retains old incidents but only the newest unresolved incident for a target is actionable; no recipe or transcript crosses incarnations. |
| E11 | Generate three loss incidents for one reused name. | Lost group shows one actionable row with an honest incident count/history; Saved panes are deduplicated; Recover All preview and claim contain that target once. |
| E12 | Run external `tmux kill-session` on an isolated tracked session. | It appears red as `force/unknown kill` within six seconds; Dismiss resolves it; no false claim that it was a segfault or intentional GUI close. |
| E13 | SIGKILL the isolated tmux server containing several sessions. | Every tracked affected session becomes Lost once; sessions on a second private socket remain live; other YOLOmux servers remain healthy. |
| E14 | Kill several isolated tmux servers/sessions nearly together. | Lost rows are grouped by typed socket/target, claims are not duplicated, browser remains responsive, and unrelated sockets stay live. |
| E15 | Simulate a boot-ID change, then perform the operator-only real reboot gate. | Previously live tracked sessions become `host reboot/outage` incidents after startup; intentional closures do not reappear; recovery state survives the outage. |
| E16 | Lose one pane/window from a multi-window, multi-pane session while the tmux server survives. | Only the dead topology member is repairable; live panes are not duplicated or killed; pane IDs and layouts remain distinct. |
| E17 | Create the same session name on two private sockets and lose only one. | UI labels the socket, only the lost typed target turns red, and recovery cannot attach to or overwrite the equal name on the other socket. |
| E18 | Recover an AI incident to its original free name. | Successful response resolves the incident, removes red, adds the live row within one second, attaches a terminal that accepts input, and continues the exact transcript with exact flags. |
| E19 | Use Shell only for AI, Term, missing-transcript, and unsupported-provider records. | Each creates a usable shell/layout without launching an AI process; the resolved incident and live row converge immediately. |
| E20 | Use Restore as new name for AI and Term records. | Name validation/conflict behavior is clear; AI resumes only when recoverable; Term restores a shell; the original Lost row resolves and the new live name appears within one second. |
| E21 | Open preflight, create a conflicting target from the second server, then submit. | Stale revision is rejected; no overwrite or duplicate process occurs; refreshed preflight offers Attach existing/new name/cancel with current facts. |
| E22 | Remove or move cwd, remove transcript metadata, or make saved flags unsupported. | Preflight shows the exact unavailable reason; automatic Recover is blocked; safe actions remain; failure leaves the red row and releases the claim. |
| E23 | Start Recover All with unique, duplicate-transcript, shared-worktree, conflicting-name, Term, and failing items; pause, resume, cancel, and retry. | Preview membership matches visible grouping, shells/layout restore first, concurrency bound holds, skipped/failing rows remain actionable, completed rows do not rerun, and no duplicate transcript/process is launched. |
| E24 | Kill server A after it acquires a recovery claim and before/after tmux creation; let server B continue. | Durable state becomes safely retryable or recovered without duplicate creation; stale claim expires or transfers; browser shows the concrete state rather than spinning forever. |
| E25 | Recover while Tabber is open, closed, page hidden, EventSource disconnected, and after browser reload. | Success uses the response/revision immediately; visibility/open/reconnect performs one bounded reconciliation; the five-second fallback never becomes the success path. |
| E26 | Manually recreate the same tmux target outside YOLOmux while its Lost row is visible. | Next immediate/open/visibility reconciliation recognizes the live incarnation, avoids auto-resume, and presents Attach existing or resolves according to the explicit identity policy without stale duplicate rows. |
| E27 | Exercise regular cookie auth only, Basic only, and both enabled across page load, SSE, polling, Recover, and WebSocket attach. | No repeated native password dialog after valid authentication; with both enabled a valid cookie request is not challenged for Basic, either accepted method works as documented, and unauthenticated requests fail without mutation. |
| E28 | Render Lost group and every preflight state in dark/light themes at desktop, 540px, and narrow mobile widths with 100% and 200% zoom. | Text and disabled explanations are readable, controls fit or scroll, focus is visible/trapped, keyboard/Escape behavior works, ARIA names match actions, and no content is clipped or covered. |
| E29 | Copy transcript ID, launch command, resume command, and flags for live/lost Claude/Codex panes. | Clipboard text exactly matches the full displayed source and process argv with no truncation, guessed transcript, changed order, or browser focus/terminal mutation. |
| E30 | Trigger recovery failure after the sheet opens: missing cwd, server error, tmux create error, and provider start error. | Modal/row reports the concrete stage and retry choices, claim clears, Lost row stays red, no phantom live row appears, and partial tmux creation is either safely retained as shell-only or cleaned up according to the displayed policy. |
| E31 | Reproduce screenshots `006` and `007` on the restarted live dev server after focused detector tests. | `006` is green RUN from the valid active counter; `007` is red needs-input from AskUserQuestion; partial Ctrl-T task chrome and the tmux status bar do not override either state. |
| E32 | Correlate an isolated tmux crash with systemd-coredump and the matching unstripped binary. | Operator output identifies executable/build and yields a symbolized backtrace; ordinary Tabber/API/share/log payloads expose no core or backtrace content. |
| E33 | Click Recover twice and submit the same incident simultaneously from both YOLOmux servers. | Exactly one claim and one tmux/provider process wins; every caller receives the same resolved incarnation or a harmless already-restoring/already-recovered result; no duplicate row or error loop appears. |
| E34 | Stop the creating server before tmux creation, during creation, and immediately after creation but before recipe reconciliation. | Pending records resolve to failed/no-session or the one observed live incarnation; no phantom Lost row, untracked live session, duplicate recipe, or duplicate provider launch survives reconciliation. |
| E35 | Use valid session names and cwd paths containing spaces, quotes, leading dashes, Unicode, and shell metacharacters; submit invalid/control-character names. | Exact argv remains tokenized without shell interpolation, valid targets recover to the exact path/name, invalid targets fail preflight, and no value is executed as injected shell/tmux syntax or rendered as HTML. |
| E36 | Make the journal read-only/full, inject a corrupt snapshot with a valid prior generation, and expose a newer unsupported schema. | Recovery fails closed with an operator-visible reason, preserves the last good/newer data byte-for-byte, does not classify intentional kills from unwritten intent, and never replaces state with an empty snapshot. |
| E37 | Advance records past retention, dismiss one incident, and keep another live/unresolved across compaction and server restart. | Only eligible resolved/expired history is pruned; unresolved Lost rows and live recipes remain; dismissal targets one incident/incarnation and cannot erase a newer reused-name record. |
| E38 | Exercise capture policy off, manual, pre-intentional-close, bounded change-triggered, and advanced continuous modes on a pane with and without an existing `pipe-pane`. | Off writes nothing, bounded modes enforce byte/age limits and sanitize terminal control bytes, existing user `pipe-pane` is never replaced, capture remains incarnation-bound, and transcript identity/recovery does not depend on captured screen text. |
| E39 | Relaunch Claude/Codex manually from the surviving shell with safe flags, positional prompt-like text, and secret-looking arguments, then use Save flags for recovery. | Only known safe non-prompt flags are proposed, user confirmation is required, unsafe/ambiguous argv is refused, and the next recovery never persists or replays prompt text or likely secrets. |
| E40 | Upgrade/downgrade the installed Claude/Codex CLI after loss so the exact recorded command is no longer supported. | Preflight names the CLI/build mismatch, automatic resume stays blocked, exact copied command remains available for inspection, and only an explicit edited launch or Shell only can proceed; YOLOmux never silently weakens flags. |
| E41 | Create Bash, Claude, and Codex sessions directly with tmux or an agent skill on the default socket while two YOLOmux servers are running. | Each session is automatically inventoried with a baseline recipe and appears in both Tabbers without a Save recipe click; provider fields are added only when uniquely attributable. |
| E42 | Create an owner-owned custom `tmux -S` socket through a registering skill, then create another through platform-discoverable same-user socket state. | Both sockets enter the durable registry/inventory exactly once, their sessions are tracked with distinct socket IDs, and a non-tmux or foreign-owned socket is rejected without information disclosure. |
| E43 | Exit an automatically adopted one-pane shell with status 0. | A matched `pane-exited`/`session-closed` sequence resolves it as `closed-cleanly`; it disappears from live Tabber, never enters Lost history, and leaves only the bounded deduplication tombstone. |
| E44 | Exit automatically adopted shells with status 1 and status 7 under `remain-on-exit failed`. | Each `pane-died` record captures the exact status before cleanup/repair; each becomes one red `lost-unclean` row with Shell-only recovery even when no provider metadata exists. |
| E45 | Terminate automatically adopted shells with SIGTERM and SIGKILL while their tmux server remains alive. | Each retained failed pane records the exact signal when tmux exposes it, becomes red once, and is recoverable; no signal death is discarded as a clean exit. |
| E46 | Observe a server/window where the user already configured `remain-on-exit on`, then exit panes with status 0 and nonzero. | YOLOmux preserves the user's option, records both statuses from `pane-died`, does not auto-remove the user's retained panes, suppresses Lost history for exit zero, and shows nonzero as recoverable without duplicating the retained live/dead representation. |
| E47 | Install hooks, stop every YOLOmux web process, then create/rename/exit sessions on the still-running registered tmux server; restart two YOLOmux servers. | The owner-only hook journal preserves lifecycle events while the web UI is absent, restart reconciles them once, clean exits stay absent, and unclean/unknown exits appear red on both servers. |
| E48 | In an automatically adopted multi-pane session, exit one pane zero, one pane nonzero, keep one live, then close the last live pane zero. | Pane-level evidence remains attached to the right incarnation, the live session/topology is not duplicated, only the failed pane is recoverable while the session lives, and final clean session closure does not erase the earlier unresolved failure. |

### Automated scenario traceability

This table is deliberately conservative. `Partial` means the named test proves only the stated subset; `Uncovered` means no direct test was found in the recovery browser, database, observer-process, or tmux-recovery modules. Do not promote a row to covered from a similarly named unit test or a source inspection.

| ID | Automated evidence | Status |
| --- | --- | --- |
| E01 | — | Uncovered: File-menu Term creation followed by clean exit on two servers. |
| E02 | `test_file_menu_agent_exit_leaves_the_same_tmux_shell_usable` | Partial: safe Claude launch/transcript display is not covered. |
| E03 | `test_external_kill_claude_recovery_preserves_dangerous_argv_and_transcript` | Covered. |
| E04 | `test_file_menu_agent_exit_leaves_the_same_tmux_shell_usable` | Partial: safe Codex launch/transcript display is not covered. |
| E05 | `test_external_kill_codex_recovery_preserves_dangerous_argv_and_transcript` | Covered. |
| E06 | `test_file_menu_agent_exit_leaves_the_same_tmux_shell_usable` | Covered. |
| E07 | `test_tmux_menu_kill_is_intentional_across_two_yolomux_servers` | Covered. |
| E08 | — | Uncovered: web-server/browser reconnect while tmux stays live. |
| E09 | `test_gui_rename_then_external_loss_uses_only_the_renamed_recovery_identity` | Covered. |
| E10 | `test_reused_session_name_recovers_only_the_newest_loss_incident`; `test_session_restore_claim_uses_only_newest_incident_for_a_reused_pane` | Covered. |
| E11 | `test_reused_session_name_recovers_only_the_newest_loss_incident` | Partial: three-incident grouping and Recover All preview are not covered together. |
| E12 | `test_external_kill_shell_recovery_converges_tabber_and_terminal_within_one_second` | Partial: explicit Dismiss behavior is separate. |
| E13 | `test_tmux_server_loss_marks_browser_shell_red_then_recovers`; `test_tmux_server_loss_on_one_socket_keeps_the_second_yolomux_browser_live` | Covered. |
| E14 | — | Uncovered: near-simultaneous loss of several servers. |
| E15 | `test_simulated_boot_change_marks_tmux_loss_as_host_outage_then_recovers_shell` | Partial: operator-only real reboot remains uncovered. |
| E16 | `test_retained_external_dead_pane_uses_tmux_exit_status_for_lost_history`; `test_retained_pane_repair_failure_keeps_the_red_tabber_incident` | Partial: live multi-window preservation is not covered. |
| E17 | `test_registered_same_name_socket_loss_recovers_from_tabber_without_touching_default` | Covered. |
| E18 | `test_external_kill_claude_recovery_preserves_dangerous_argv_and_transcript`; `test_external_kill_codex_recovery_preserves_dangerous_argv_and_transcript` | Covered. |
| E19 | `test_external_kill_shell_recovery_converges_tabber_and_terminal_within_one_second`; `test_external_kill_term_restore_as_new_name_converges_and_attaches_a_shell` | Partial: missing-transcript and unsupported-provider cases remain uncovered. |
| E20 | `test_external_kill_term_restore_as_new_name_converges_and_attaches_a_shell`; `test_mixed_three_pane_recovery_resumes_codex_and_restores_shells` | Covered. |
| E21 | `test_mixed_three_pane_recovery_resumes_codex_and_restores_shells` | Partial: stale revision from a second server is not covered. |
| E22 | `test_mixed_three_pane_recovery_resumes_codex_and_restores_shells` | Partial: repository drift only; missing cwd, transcript, and unsupported flags remain uncovered. |
| E23 | `test_recover_all_restores_only_the_selected_lost_session`; `test_recover_all_continues_after_a_failed_member_and_keeps_it_retryable`; `test_recover_all_pause_then_resume_serializes_the_next_session`; `test_recover_all_cancel_does_not_launch_pending_sessions` | Partial: duplicate transcripts, shared worktrees, and conflict preview remain uncovered. |
| E24 | `test_two_servers_racing_shell_recovery_create_exactly_one_tmux_session` | Partial: creating-server death at each transaction boundary remains uncovered. |
| E25 | `test_external_kill_shell_recovery_converges_tabber_and_terminal_within_one_second` | Partial: hidden/open/reload/EventSource cases remain uncovered. |
| E26 | `test_external_recreation_attach_existing_resolves_lost_group_and_attaches_terminal` | Covered. |
| E27 | — | Uncovered: cookie, Basic, and combined auth across recovery mutation and streams. |
| E28 | `test_multi_agent_recovery_sheet_lists_every_session_id_and_explains_ordered_actions` | Partial: themes, responsive widths, zoom, keyboard, and ARIA matrix remain uncovered. |
| E29 | `test_live_7771_session_popover_shows_copyable_pane_bound_provider_session_ids` | Partial and opt-in: commands, flags, and Lost rows remain uncovered. |
| E30 | `test_failed_shell_recovery_keeps_the_red_row_and_retry_recovers` | Partial: missing cwd, tmux, and provider start failures remain uncovered. |
| E31 | — | Uncovered: live detector screenshot journey. |
| E32 | `test_opt_in_coredump_evidence_is_private_bounded_and_not_returned_as_text`; `test_coredump_collector_accepts_systemd_json_array_and_pre_recorded_server_identity` | Partial: operator symbolization remains uncovered. |
| E33 | `test_two_servers_racing_shell_recovery_create_exactly_one_tmux_session` | Covered. |
| E34 | `test_creating_record_reserves_its_session_from_automatic_adoption`; `test_creating_record_can_be_filtered_to_supervisor_owned_launches`; `test_precreate_recipe_never_becomes_a_phantom_lost_session` | Partial: real creating-server death windows remain uncovered. |
| E35 | — | Uncovered: special-character path/name and invalid-name journey. |
| E36 | `test_future_recovery_schema_is_rejected_without_reinitializing_or_rewriting`; `test_corrupt_legacy_state_is_preserved_byte_for_byte_and_not_imported`; `test_recovery_write_failure_keeps_the_last_durable_snapshot` | Partial: read-only/full mutation and browser-visible failure remain uncovered. |
| E37 | `test_compaction_expires_closed_and_lost_history_after_retention` | Covered. |
| E38 | — | Uncovered: capture-policy matrix. |
| E39 | `test_tabber_save_flags_for_recovery_confirms_a_safe_live_codex_recipe`; `test_safe_provider_argv_keeps_only_known_flags_and_never_a_prompt` | Partial: Claude and explicit confirmation variants remain uncovered. |
| E40 | — | Uncovered: recorded CLI/build mismatch. |
| E41 | `test_externally_created_bash_is_automatically_adopted_then_shell_recovered`; `test_observed_agent_enriches_an_existing_shell_recipe_without_changing_its_incarnation` | Partial: direct Claude and Codex sessions on the default socket remain uncovered. |
| E42 | `test_registered_tmux_socket_is_owner_validated_and_persists_after_server_loss`; `test_registered_same_name_socket_target_receives_the_correct_browser_websocket_input` | Partial: registering-skill and platform-discovery paths remain uncovered. |
| E43 | `test_supervising_shell_exit_is_durably_classified_in_tabber`; `test_shell_exit_records_clean_and_unclean_evidence_before_tmux_inventory` | Covered. |
| E44 | `test_retained_external_dead_pane_uses_tmux_exit_status_for_lost_history`; `test_user_remain_on_exit_preserves_clean_and_failed_external_pane_evidence` | Covered. |
| E45 | `test_retained_external_term_signal_death_stays_red_with_tmux_evidence` | Covered. |
| E46 | `test_user_remain_on_exit_preserves_clean_and_failed_external_pane_evidence` | Covered. |
| E47 | `test_recovery_observer_process_persists_loss_without_a_web_server`; `test_observer_keeps_unexpected_loss_history_while_every_web_server_is_absent` | Partial: hook installation and two-server restart reconciliation remain uncovered. |
| E48 | — | Uncovered: automatically adopted mixed multi-pane exit sequence. |

## Evidence And Timing Gate

For each scenario, save raw artifacts only under a unique `/tmp/yolomux-recovery-e2e-<run>/` directory. The run manifest must identify the exact checkout and generated bundle, scenario ID, timestamps, browser/server/tmux process IDs, socket and state paths, auth mode, API status, journal revisions, observed UI transition times, screenshots, console errors, and cleanup result. Redact credentials, prompts, transcript bodies, environment values, and core contents.

Required assertions are evidence-first:

- Capture `tmux list-sessions`, windows, panes, pane PIDs, cwd, and current command before the loss, after the loss, after the recovery response, and after terminal attachment.
- Capture the journal record/revision and tmux hook event sequence at the same boundaries and prove the browser's displayed identity, transcript ID, flags, exit status/signal, incident count, and action mode match it.
- Measure from the successful HTTP response to removal of the Lost row and appearance of the live row. Both must occur within one second on the isolated local environment without waiting for the fallback poll.
- Send a unique line into the recovered terminal and assert it is observed in that recovered pane. A session merely appearing in `tmux list-sessions` is not enough.
- Assert no duplicate Lost rows, saved panes, Recover All items, tmux sessions, provider processes, transcript claims, or terminal WebSockets.
- Assert unrelated control sessions on both sockets and the second YOLOmux server remain alive after every destructive case.
- Capture browser console errors and unhandled rejections for the whole journey; any new error fails the scenario even if the final screenshot looks correct.
- Repeat the exact focused failure after a fix, then run all E01-E48 applicable automated rows from a clean isolated state, then run `python3 tools/check.py`, then repeat one full externally created session -> automatic adoption -> rename -> unclean loss -> preflight -> recover -> terminal-input journey against the rebuilt bundle.

Do not mark a DOIT checkbox complete from a green source regex test, store unit test, direct endpoint call, manual journal repair, or screenshot of only the final state. The evidence must cover the state transition that previously failed.

## Release Decision

Recovery is ready only when all DOIT implementation items are complete, the matrix has a clean evidence manifest, the real Claude/Codex resume smokes pass with exact flags, the operator reboot gate has passed once on the target platform, the canonical gate is green, and the final full browser journey passes after that gate against the exact rebuilt bundle. Any mismatch among tmux, journal, server registry, browser metadata, Lost rows, live rows, or terminal attachment reopens the owning checkbox; it is not a cosmetic follow-up.
