# YO!agent Skills

YO!agent skills are local product instructions for YOLOmux's built-in assistant. They are not Codex or Claude skills, and they are not arbitrary executable plugins. A YO!skill teaches YO!agent how to decide, phrase, monitor, or coordinate work; YOLOmux server code still owns the actual tools, auth checks, target resolution, risk checks, audit events, and pane sends.

## Locations

- Built-in skills ship read-only with YOLOmux under `yolomux_lib/yoagent/builtin_skills/*.yaml`.
- Built-in context ships read-only under `yolomux_lib/yoagent/builtin_context/*.md`.
- User-local skills live in `~/.config/yolomux/skills.d/*.yaml`.
- User-local context lives in `~/.config/yolomux/context.d/*.md`.

YOLOmux loads built-ins first, then overlays user-local skills by `name`. A user-local skill with the same `name` replaces the built-in; `enabled: false` disables that skill. Context files are Markdown snippets that are added to YO!agent's prompt context.

## Skill Schema

```yaml
name: wait-then-run
kind: workflow
description: Wait for one session to become idle or need input, then send a reviewed prompt to that same agent.
tools:
  - read_activity
  - watch_session
  - preview_send_prompt
  - execute_confirmed_send
triggers:
  - wait for session 6 to finish, then tell it to run python3 tools/check.py
confirmation: none
default_timeout_minutes: 120
```

Required fields are `name` and a useful `description`. `name` must match `[a-z][a-z0-9-]{1,63}` and must match the YAML file stem for user-local files. `tools` can name only server-known capability labels: `read_activity`, `recommend_next_work`, `watch_session`, `watch_all_sessions`, `notify_user`, `read_skill_files`, `write_skill_file`, `delete_skill_file`, `preview_send_prompt`, `execute_confirmed_send`, and `summarize_sessions`.

## Examples

Create a local status skill:

```yaml
name: local-status
kind: workflow
description: Ask an idle target agent what changed, what is blocked, and what it recommends doing next.
tools:
  - read_activity
  - preview_send_prompt
  - execute_confirmed_send
triggers:
  - ask session 1 for local status
confirmation: none
default_timeout_minutes: 30
```

Create a personal recommendation context file at `~/.config/yolomux/context.d/work-ranking.md`:

```markdown
Prefer work that is waiting for user input, has failing tests, or has a dirty repo with a clear next command. De-prioritize stale sessions unless the transcript says they are blocked.
```

Disable a built-in skill:

```yaml
name: work-next
enabled: false
```

## Managing Files

You can edit the files directly, or ask YO!agent to manage them:

- `list my YO!skills`
- `read skill local-status`
- `create skill local-status description: Ask idle agents for status and next steps.`
- `update skill local-status` with fenced YAML
- `delete skill local-status`

The file-management API is admin-only: `GET /api/yoagent/skill-files?kind=skill&name=local-status`, `POST /api/yoagent/skill-files/upsert`, and `POST /api/yoagent/skill-files/delete`. It validates names, canonical paths, YAML, and allowed tools before writing or deleting files.

## Coordination Rules

### Transports

YO!agent skills describe intent; the server selects the transport. Existing visible Claude/Codex panes use `tmux-legacy`, a verified tmux paste-plus-Return fallback with preflight and post-send checks. Managed providers such as Claude SDK, Claude Channels, Codex SDK, Codex app-server, Codex MCP server, `codex-exec`, and Claude stream-json are separate transports with different guarantees around result events, auth, sandbox, opt-in session identity, and completion semantics. A skill should request `preview_send_prompt`, `execute_confirmed_send`, or a watch tool instead of telling an agent to use tmux or contact another session directly.

### Perspectives

YO!agent keeps two perspectives separate. The user talks to YO!agent about routing, scheduling, and coordination; each target agent receives only the task/question from that target agent's own point of view. For `ask agent 1 to <do ...>`, the prompt sent to agent `1` is `<do ...>`, not `ask agent 1 to <do ...>`. For `ask session 1 what it has done today`, the prompt sent to session `1` is `what have you done today?`, while YO!agent may still tell the user it is asking tmux session `1`.

YO!agent is the default orchestrator. For multi-session handoffs, YO!agent asks the first session, waits for the response, derives the next prompt itself, verifies the next session is accepting an AI prompt, then sends a clean task or question. Target sessions do not know about each other unless the user explicitly asks for disclosure or relay/chaining.

Direct relay/chaining between target agents is rare. If the user explicitly requests it, YO!agent must pass concrete relay instructions instead of leaving the target agent to infer routing.
