# YOLOMux TODOs

YOLOMux-specific follow-ups and roadmap notes. Keep project-specific UI, terminal, AUTO, and session-management ideas here instead of the general Dynamo TODO list.

---

## 2026-05-24: queue YOLOMux improvements from competitor research

Research context: nearby tools include Agent Cockpit and OctoAlly for Claude/Codex web dashboards, dmux and ccmux for tmux-backed multi-agent workflows, OneCmd for tmux capture/send-keys management, rAgent/Agent Watch for remote monitoring and alerts, TrainShell for browser terminals on GPU hosts, rzr for thin tmux-to-browser access, Cogpit/ccboard for Claude observability, and Agent Approve/codex-yolo for approval infrastructure. YOLOMux's useful niche is the local Python + tmux browser dashboard with Dynamo-aware branch/PR/Linear metadata, AUTO, AI summary, paste/upload, and pane layout.

### Feature queue

- [ ] Approval queue with risk labels. Source: Agent Cockpit / Agent Approve. Why: YOLOMux AUTO is useful but opaque; show pending approval text, risk level, allow/deny, and last decision. How: extend `auto_approve_tmux.py` worker status with pending command/context and render a per-session queue panel plus history.
- [ ] AUTO audit history. Source: Agent Approve / Agent Cockpit. Why: AUTO needs traceability; users should know what was approved, blocked, or skipped. How: persist a small ring buffer in `~/.config/yolomux/state.json` or separate JSONL under `~/.config/yolomux/`, keyed by session.
- [ ] Agent state classification. Source: dmux / rAgent / Agent Watch. Why: tabs should say `working`, `waiting`, `idle`, `blocked`, `needs approval`, or `tests running`, not just connected. How: combine tmux pane activity deltas, prompt-pattern detection, AUTO state, and optional lightweight LLM classification after quiet periods.
- [ ] Browser notifications. Source: ccmux / rAgent / Agent Watch / rzr. Why: the main pain is knowing when one of many sessions needs attention. How: request browser notification permission and notify on `needs input`, `AUTO blocked`, `terminal disconnected`, `tests failed`, or `PR ready`.
- [ ] Session timeline. Source: Agent Cockpit / Cogpit / Agent Watch. Why: raw terminal output is noisy; a timeline should show prompt started, command ran, file changed, approval requested, summary generated, PR status changed. How: collect events from YOLOMux endpoints, AUTO worker decisions, transcript summaries, and git/PR polling into a compact chronological view.
- [ ] Better launch flow. Source: dmux / OctoAlly. Why: `+ Claude`, `+ Codex`, `+ Term` is fast but too limited for new work. How: add a launch dialog for working directory, initial prompt, branch/worktree mode, model/profile, and permission mode; keep the quick plus path for defaults.
- [ ] Worktree-aware session creation. Source: dmux. Why: parallel agents are cleaner in isolated worktrees than many sessions sharing one checkout. How: optional launch mode creates `git worktree`, branch, tmux session, and initial agent prompt together; keep existing tmux-only mode.
- [ ] File and PR diff panel. Source: OctoAlly / Agent Cockpit. Why: users need to inspect agent changes without leaving YOLOMux. How: add a read-only changed-files list and unified diff view per session using the session cwd; wire PR/CI links already detected in metadata.
- [ ] Needs-me filter. Source: Agent Watch / rAgent. Why: when many sessions exist, YOLOMux should prioritize sessions requiring human action. How: add a top-level filter/sort mode that brings `needs approval`, `waiting`, and `blocked` panes/tabs first.
- [ ] Resume conversation picker. Source: ccmux. Why: starting agents is only half the workflow; resuming recent Claude/Codex threads should be one click. How: index known Claude/Codex transcript/session files for current cwd, show recent conversations, and launch `claude --resume` or Codex equivalent when available.
- [ ] Mobile focus mode. Source: rzr / rAgent / ccmux. Why: YOLOMux should be usable away from the desk. How: add a single-pane mobile layout with larger terminal controls, Esc/Tab/Ctrl buttons, paste/upload, and approval actions.
- [ ] Host and process vitals. Source: TrainShell / rAgent. Why: Dynamo/GPU work benefits from seeing CPU/GPU/memory/load and per-session process health. How: add lightweight local probes for CPU/mem/load, optional `gpu-info`, and session process trees; render in the topbar or per-session info drawer.
- [ ] Per-session AUTO policy. Source: dmux / Agent Approve. Why: AUTO should not be one global binary button. How: support `off`, `read-only`, `edits`, `safe commands`, and `full bypass`-style policies per session; expose policy in the AUTO button/menu and persist it.
- [ ] Structured AI summary state. Source: Cogpit / Agent Watch plus current YOLOMux summary. Why: summary should feed UI state, not just prose. How: make AI summary emit current task, last action, blocker, branch, PR, next action, and confidence; render a compact session card.
- [ ] Multi-machine connector. Source: rAgent / TrainShell / ccmux. Why: a future YOLOMux could view tmux sessions across several machines. How: design a tiny remote agent that reports tmux sessions, metadata, vitals, and WebSocket terminal streams back to one YOLOMux instance; keep local-only as the default.

### Suggested order

1. Add `needs input` / `working` / `idle` / `blocked` state detection per tab.
2. Add AUTO audit panel with approved/blocked command history.
3. Add browser notifications for `needs input` and `AUTO blocked`.
4. Add per-session info drawer with branch, PR, CI, changed files, and latest structured summary.
5. Add launch dialog with directory, agent, model/profile, permission mode, and initial prompt.
6. Add optional worktree/session creation.
