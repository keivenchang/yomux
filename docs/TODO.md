# YOLOmux TODOs

Roadmap notes for YOLOmux. Keep active product, architecture, and refactor work here. Put completed work in [`DONE.md`](DONE.md), GUI behavior contracts in [`specs/GUI.md`](specs/GUI.md), YO!share replay details in [`specs/SHARE_MIRRORING.md`](specs/SHARE_MIRRORING.md), and build/restart/CPS commands in [`DEVELOPMENT.md`](DEVELOPMENT.md).

## Product Direction

YOLOmux should stay a lightweight local tmux browser control plane. The useful niche is a fast local UI for existing Claude/Codex/tmux sessions with clear state, safe YOLO controls, repo metadata, file paste/upload, and low-friction attach/reply.

Borrow from other tools only when the feature improves the local control loop: know which session needs attention, understand what changed, approve or block risky work, and jump back into the right terminal quickly. Structured agent channels are the durable direction for approvals, state, and controlled sends; tmux capture and paste remain necessary fallbacks for already-open visible panes.

## Orientation

- Build, test, restart, and CPS commands live in [`DEVELOPMENT.md`](DEVELOPMENT.md). The local gate is `python3 tools/check.py`.
- AI-agent behavior rules and repo lessons live in [`../AGENTS.md`](../AGENTS.md). Do not re-inline those rules here.
- Code map: entry `yolomux.py` -> `yolomux_lib/cli.py`; HTTP routing `yolomux_lib/server.py`; app state and tmux actions `yolomux_lib/app.py`; session/agent discovery `yolomux_lib/sessions.py`; repo/PR/CI metadata `yolomux_lib/metadata.py`; file ops `yolomux_lib/filesystem.py`; shared helpers and paths `yolomux_lib/common.py`; HTML shell `yolomux_lib/web.py`; frontend partials `static_src/js/yolomux/*.js` and `static_src/css/yolomux/*.css` generate `static/yolomux.js` and `static/yolomux.css`.
- State lives in `~/.config/yolomux/state.json`; events live in `~/.local/state/yolomux/events.jsonl`; YO!agent skill files live under `~/.config/yolomux/skills.d/` and context under `~/.config/yolomux/context.d/`.
- Line numbers drift. Search by symbol, route, CSS class, setting key, or test name before editing.
- `DOIT*.md` files are active work queues. When a queue is fully implemented and validated, archive the result in [`DONE.md`](DONE.md) and remove the queue file.

## Active Queue Follow-Ups

- [ ] [M] Background-owner live fleet verification. When it is OK to restart or drive Linux `7770`, `7771`, `7772`, and `7773`, verify startup ownership order, `7771` takeover after restart, multi-port Tabber/Finder cache writes, shared-root Quick Open/search indexing, follower worker thread absence, and UI responsiveness during a large index rebuild. The implementation and isolated `8004`-`8007` verification are already complete; this was moved out of `DOIT.single_owner_background_indexing.md` because it is live fleet validation, not unfinished source work.
- [ ] [M] License history remote finalization. Only after explicit force-push authorization, push the rewritten PolyForm Noncommercial history with `--force-with-lease`, then verify a fresh clone against the key license searches. The current-tree migration, local history rewrite, and local verification are already complete.

## Current Priorities

- [ ] [XL] Reliable structured agent control. Replace as much scrape-and-type approval/send behavior as possible with structured channels: Claude permission hooks for Claude decisions, Codex app-server/SDK/MCP where YOLOmux owns or can safely resume the conversation, and `tmux-legacy` only as the verified visible-pane fallback.
- [ ] [XL] Layout/render reconciliation. Move the grid, topbar, tab strips, virtual tabs, and pane chrome toward one keyed renderer driven by layout state, with fewer pane-type special cases.
- [ ] [XL] Edge-pinned virtual tabs. Fold Finder/Differ/Tabber into a generic pinned-edge model with declarative placement, hidden-by-user state, minimum sizes, and adoption rules instead of bespoke Finder placement code.
- [ ] [L] Worktree-aware parallel agents plus task hub. Add a launch path that creates an isolated worktree/branch/tmux session and a task hub that shows review, in-progress, and complete states across sessions.
- [ ] [L] Responsive/mobile layout. Add a single-pane mobile focus mode, touch-friendly tab navigation, larger controls, upload/paste affordances, and enough editor/diff behavior to check in from a phone.
- [ ] [L] Multi-machine connector. Defer until the local product is stable; this changes auth, networking, logging, and failure modes.

## YO!agent

- [ ] [M] Add remaining watch predicates on top of persisted YO!agent jobs: tests-finished, all-agents-status fanout, review sweep, close-out finished work, and pause noisy watches. Needs-input watches, blocked-session watches, done-after-working watches, and cancel pending jobs by session are shipped in DONE 2026-06-20.
- [ ] [M] Add artifact-handoff helpers: choose a safe project-local path, ask a target to write there, validate existence/size/type, and pass the path or bounded content to the next target.
- [ ] [L] Add golden-frame fixtures for remaining scrape-and-type paths. Record real Claude/Codex `capture-pane` frames per agent version and pin spinner/footer/approval/ready detection so upstream TUI changes fail tests instead of silently changing behavior.
- [ ] [L] Expose YOLOmux as a local MCP/ACP-style control plane so agents can query session/activity state and request server-verified sends through structured APIs instead of requiring YO!agent to paste into panes.

## YOLO Approval And Audit

- [ ] [L] Add an approval queue view for pending high-risk actions. Start read-only if live allow/deny interception is hard.
- [ ] [M] Add per-session YOLO policy. Initial modes: `off`, `prompt-only`, `safe`, `edit`, `full`. Make policy visible on the tmux-session YOLO control.
- [ ] [L] Add rule scoping dimensions: global vs per-repo vs per-session, per-agent (`claude` / `codex`), and per prompt type (`bash` / `file` / `tool`).
- [ ] [M] Decide how users edit rules. Current path is raw YAML through `Open rule file`. Keep raw-YAML-only if the template plus validation is enough; otherwise add a structured rule editor with add/remove/reorder, dropdowns for `type`/`action`/`risk`, a `match` list editor, and top-level `default`.
- [ ] [M] Add risk-policy profiles on top of the existing first-match-wins YAML engine. Keep risk labels concrete: `read`, `edit`, `network`, `process`, `delete`, `credential`, `unknown`.

## Layout, Finder, Differ, And Tabber

- [ ] [M] Finish remaining app menu gaps: panel-tab visibility controls, inactive-tab tray/show-all control, remaining YOLO controls under `tmux`, and per-pane peek/reply actions.
- [ ] [M] Add git-aware Finder metadata. Repo rows and rooted paths should show repo name, branch, dirty/ahead/behind, and remote/GitHub URL through cached server metadata, not one git spawn per hover.

## Editor And Preview

- [ ] [M] Add remaining editor power keys through CodeMirror where possible: multi-cursor, select all occurrences, add cursor above/below, line move/copy/delete, smart-select, matching bracket, fold/unfold, symbol jump, and command mode. Avoid app-side Ctrl-letter bindings on Mac.

## YO!share

- [ ] [M] Exchange the first valid token hit for a short-lived HttpOnly guest cookie, then redirect to a clean URL so bearer tokens leave browser history and Referer paths.
- [ ] [L] Continue moving presenter state through host-owned replay rather than semantic client inference. Layout, active pane, scroll, menus, popovers, YO!info, Finder/Differ/Tabber, editor state, and terminal placeholders should converge from host frames.
- [ ] [L] Add presenter-follow polish: host active pane, per-pane scroll, and host pointer/ghost-cursor frames. Apply them on viewers without echoing client-authored state back.

## Launch, Worktrees, And Vitals

- [ ] [L] Evaluate hidden-document terminal WebSocket suspension as an isolated feature. Use a bounded grace period and prove no lost input, duplicate socket, resize-authority drift, scrollback loss, missed attention transition, or incomplete current-snapshot recovery before enabling it; demand-aware HTTP/SSE gating intentionally leaves terminal sockets connected until this proof exists.
- [ ] [L] Add a launch dialog behind `+ Claude`, `+ Codex`, and `+ Term` with cwd, agent, model/profile, permission mode, initial prompt, optional session name, and optional worktree-backed launch.
- [ ] [M] Add a resume picker for recent Claude/Codex conversations scoped to the selected cwd.
- [ ] [M] Add a peek/reply action for a session when it needs only a short response and the user does not need to attach to the full terminal.
- [ ] [M] Add worktree cleanup guardrails: never delete a worktree with uncommitted changes; show the path and stop.
- [ ] [M] Add per-session token/cost/context metrics only if they can be read reliably from Claude/Codex metadata without scraping fragile terminal text.
- [ ] [M] Add lightweight CPU/memory/load probes and per-session process trees. Add optional `nv-smi` GPU status when available, but do not make GPU support required.

## Global Summaries

- [ ] [XL] Add durable session-summary context without reintroducing a recurring background loop. The current product contract is first-launch-only transcript summaries per server run; future summary work should update on first visible YO!agent launch, explicit user refresh, or bounded event-driven jobs, and must keep incremental summaries (`prior_summary + transcript delta`) so it never re-sends full transcripts each tick.
- [ ] [M] Extend the transcript/file-activity working-directory inference to Finder sync, per-tab jump-to-working-path, and summary context. Session repo metadata already uses it: `candidate_session_cwds` feeds each agent's edited dirs into `session_git_inventory` and the YO!info repo list, so a session launched from `$HOME` still surfaces the real repo/branch (see DONE 2026-06-17). Remaining: Finder sync, per-tab jump, and summary context.

## Internationalization

- [ ] [M] Finish structured localization for the auxiliary tmux-wall shell and remaining browser-visible share/upload/filesystem/search/run-history/transcript/API errors while retaining raw OS/git/tmux/model text only as diagnostic detail.

## Product Guardrails

- Keep `Needs input` / `Working` / `Done` grouping high on the roadmap because it makes every existing pane easier to manage without changing tmux architecture.
- Build event/audit data before timeline UI. The data model matters more than a visual feed.
- Keep tmux as a core differentiator. Do not replace it with a heavier runtime just to match orchestration products.
- Defer visual canvases, broad multi-machine orchestration, and full pipeline boards until the local control loop is stronger.
