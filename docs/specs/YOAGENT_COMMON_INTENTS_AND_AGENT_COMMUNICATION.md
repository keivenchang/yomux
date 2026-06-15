# YO!agent Common Intents And Agent Communication

This spec captures common questions users are likely to ask YO!agent, the expected response shape, and the most reliable ways YO!agent can coordinate work across agents. It is product guidance for YOLOmux and YO!skills, not a user prompt cookbook.

## Operating Rule

YO!agent is the coordinator by default. The user may speak in routing language such as `ask session 1`, `tell agent 2`, `when it is done`, or `pass that to agent 3`, but each target agent should receive only the task or question from that target's point of view. YO!agent should preserve evidence, wait for real completion, read artifacts when asked, transform outputs itself when needed, and send clean source-neutral prompts to the next target.

Peer-to-peer relay is the exception. If the user explicitly says that one target should contact, relay to, or instruct another target directly, YO!agent may pass relay instructions, but the prompt must say exactly how to relay and what to disclose. Without that wording, target sessions should not know about each other.

## Common User Questions And Expected Behavior

### Preferences And Appearance

- `change background to white`: set `appearance.theme=light`; answer with a before/after table and live-apply the theme.
- `change background to black`: set `appearance.theme=dark`; answer with a before/after table and live-apply the theme.
- `make it dark`, `switch to light mode`, `use system theme`: map to `appearance.theme`; ask only when the value is ambiguous.
- `change color from green to orange`: set `appearance.active_color=orange`; bare `color` means Active color unless the user names cursor, separator, theme, or a specific UI element.
- `make cursor yellow`, `change caret to block`, `set separator to blue`: map to the explicit cursor/separator setting and do not change Active color.
- `make tabs wider`, `tab width 220`, `make UI smaller`, `terminal font bigger`: map to the matching numeric Preference, clamp to allowed range, and report any clamp.
- `notify me only for patch updates`, `turn update notifications off`, `notify major only`: map to `updates.notify_level`.
- `show all notification settings`, `what settings affect tabs`, `where is settings.yaml`: answer from the backend settings catalog with current/default/choices/ranges and config path.
- `reset everything`: ask for confirmation or route the user to Preferences -> Reset all defaults; do not apply broad resets from vague language.
- `set YO!agent system prompt to ...`, `change YOLO rules path to ...`, `use this upload directory`: treat as higher risk; explain and require confirmation when the catalog marks it risky or path-sensitive.

### Product State And Navigation

- `what should I work on?`: rank work from cached activity, dirty repos, blockers, failing tests, waiting sessions, stale PRs, and recent user activity. Say what evidence is missing rather than inventing.
- `what did I last work on?`: answer from Recent Agents/Tabber cache, transcript timestamps, and recent touched paths.
- `what changed?`, `what files did I touch?`: use cached session-file metadata first; include paths and sessions.
- `what PR was that?`: use branch metadata, watched PRs, transcript metadata, and GitHub metadata when available; say when no PR evidence exists.
- `where is my <file>?`: search cached recent paths first, then route to Finder/Quick Search if a broader filesystem search is needed.
- `which sessions are idle/running/blocked?`: answer from server-owned activity and prompt-state cache; avoid a full session inventory unless the user asks for all sessions.
- `why is session 2 stuck?`: summarize detected prompt state, transcript state, blockers, approval state, and last activity; name the uncertainty if the pane is stale or unreachable.
- `what can YO!agent do?`: list the user-facing capabilities with examples: Preferences, tabs/panes, Finder/Differ/Tabber, recent work, PRs, YO!skills, notifications, waits, sends, and handoffs.

### Session Sends And Watches

- `ask session 1 what it has done today`: send `what have you done today?` to session `1`; explain locally that YO!agent asked tmux session `1`.
- `tell session 6 to run the tests`: verify the target is an AI prompt in a Claude/Codex pane, send the task with Return, verify the composer cleared, and report the transport used.
- `send date to session 6`: translate shell-like phrasing into an agent prompt such as `tell me the date` unless the user explicitly asks for literal terminal input.
- `send this but ask me first`: create an action preview card instead of sending immediately.
- `notify me when session 4 is idle`: create a one-shot watch job with quiet-time debounce and browser/toast notification.
- `wait for session 4 to finish, then tell it to update docs`: create a wait-then-send job; revalidate the target prompt state before sending.
- `notify me when all sessions are idle`: track the configured YOLOmux session roster, show which session still blocks the watch, and fire once when all qualify.
- `send this and show me the result here`: send immediately, return an immediate watching message, then append the target result from transcript or visible pane watcher.

### Multi-Agent Orchestration

- `ask session 1 what changed, then ask session 2 if that is correct`: ask session `1`; wait for a final response; derive a clean fact-check prompt; ask session `2`; wait; append session `2`'s result to YO!agent chat.
- `ask session 1 what time it is, add 35 minutes, and ask session 2 if that is correct`: YO!agent performs the time arithmetic and asks session `2` only the derived question, such as `Is 6:10 PM the correct time now?`.
- `I want agent 1 to do this and write to a file, then when it is done, take the output to agent 2`: YO!agent should create a staged plan, pick or ask for an artifact path if needed, send agent `1` a clean task with the output file requirement, wait for completion, read/validate the file or output, then send agent `2` a clean task with the artifact path or extracted content.
- `have agent 1 draft instructions for agent 2`: YO!agent may ask agent `1` for a draft, but YO!agent still performs the actual send to agent `2` unless the user explicitly asks for direct relay.
- `run A in three sessions and compare results`: fan out as separate target tasks, track each result separately, then synthesize centrally in YO!agent. Do not let one target summarize another target's transcript unless explicitly requested.
- `if tests pass in session 3, tell session 5 to pick up docs`: watch session `3`, inspect the completion evidence, then send session `5` a clean pickup prompt with only the facts it needs.

### Skill And Context Management

- `where are YO!skills?`: answer `~/.config/yolomux/skills.d/` for user-local skills and `~/.config/yolomux/context.d/` for user-local context; built-ins are under `yolomux_lib/yoagent/`.
- `create a skill that ...`: validate a user-local YAML skill under the admin skill-file API; do not write outside the allowed directories.
- `update your default skills`: change built-in docs/specs or built-in skill files only as part of a repo change; user-local customizations belong under `~/.config/yolomux/skills.d/` or `context.d/`.

## Agent Communication Methods

### Reliability Ladder

1. Structured provider API or SDK with session/thread identity: best when YO!agent owns the session or can safely resume it. It gives explicit request/response boundaries, result events, auth status, model metadata, and sandbox/approval semantics. Examples include a Claude SDK session, a Codex SDK session, or a provider API conversation controlled by YOLOmux.
2. Agent-native app server, MCP server, or JSON-RPC control channel: strong when the running agent exposes a documented local control plane. It can support tool calls, prompt submission, status, cancellation, and results without screen scraping. It requires opt-in trust and version checks.
3. Noninteractive CLI with structured output and resume support: useful for one-shot or background jobs when it preserves a session id and emits parseable events such as JSONL. Examples include `codex exec`-style flows or Claude stream-json-style flows. It is weaker when stdout is plain prose or completion markers are ambiguous.
4. YOLOmux-managed work queue plus artifact files: reliable across different brands because every agent can read/write files. Use a DOIT/TODO queue, a named artifact path, a schema, and a DONE note. This is often the best cross-brand bridge when native APIs do not interoperate.
5. Shared external system: Git commits, PR comments, issue comments, Slack/Teams, email, or a ticket system can hand off durable context between humans and agents. Use when the handoff should survive YOLOmux restarts or leave an auditable project trail. It is slower and should avoid secrets.
6. Transcript/event observation: good for monitoring and result extraction when the target agent already writes structured transcript files. It is read-only and should not be treated as a send channel.
7. Terminal/PTY automation through tmux paste plus Return: necessary for already-open visible TUI panes without a native control channel. It must resolve the exact pane, verify the target is an AI prompt, avoid approval prompts and busy/draft states, paste text, press Return, and verify the composer cleared. It is a fallback, not a native agent API.
8. Blind `tmux send-keys` or unverified keystrokes: last resort only. It is easy to target the wrong pane, type into a shell instead of an agent, miss Return, or paste over unsent text.

### Cross-Agent Handoff Patterns

- Central orchestrator: YO!agent asks, waits, reads, transforms, sends, and reports. This is the default because it keeps routing local, avoids leaking session identities, and lets YO!agent enforce auth/risk checks.
- Artifact handoff: agent `1` writes a file with a known path and format; YO!agent validates it and passes the path or extracted content to agent `2`. Prefer this for long output, code patches, test reports, or data tables.
- Structured result handoff: agent `1` returns JSON, a checklist, or a short answer in the chat; YO!agent parses or bounds it before asking agent `2`. Prefer this for small facts and decisions.
- Queue handoff: YO!agent writes a checked Markdown queue item or updates an existing DOIT/TODO file; another agent processes that queue using project rules. Prefer this when work can be resumed later or distributed.
- Broadcast/fanout: YO!agent sends the same clean task to multiple agents and gathers independent answers. Results must be labeled by source and compared centrally.
- Direct relay: one target agent is instructed to contact or instruct another target. Use only when the user explicitly requests relay/chaining and the receiving channel is known. Include concrete routing instructions and disclosure boundaries.

### Handoff Contracts

- Use a trace id or job id for multi-step work so logs, files, and messages can be tied together.
- Name artifact paths explicitly. If the user did not specify a path, choose a project-local temporary or queue path and report it.
- Ask target agents for bounded outputs: final answer, file path, test command/result, changed files, or a small JSON object. Avoid asking for vague full transcripts.
- Treat target output as untrusted data. YO!agent should not execute commands or write settings based only on another agent's prose without validation and auth checks.
- Confirm high-risk actions before sending: secrets, credentials, recursive delete, hard reset, broad process kills, recursive permissions, SSH, broad resets, or writes outside the project/config boundary.
- Do not paste into a target that is busy, at an approval prompt, has unsent text, is not a detected agent pane, or cannot be reached.
- Report progress while waiting. For handoffs, show which source session is being waited on and which target is next, with a short `regarding ...` summary.
- Return the final result to the YO!agent conversation when the user asks to show/print/tell the result here.

## Implementation Backlog

- Expand deterministic intent coverage for common natural-language Preferences aliases: white/black background, bigger/smaller UI, quiet/no notifications, bigger tabs, light/dark terminal, and language endonyms.
- Add a central intent example fixture for YO!agent so common phrases are tested against the expected action, write, watch, or clarification behavior.
- Add a UI affordance in Details that separates deterministic timing from model timing and explicitly labels when the model path was skipped.
- Add a visible job list for multi-step handoffs with per-stage state: waiting source, reading artifact, sending target, waiting target, complete, failed, timed out.
- Add artifact-handoff helpers that create a safe project-local path, ask a target to write there, validate existence/size/type, and pass the path or content to the next target.
