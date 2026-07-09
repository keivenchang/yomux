# YO!agent Skills

YO!agent skills are local product instructions for YOLOmux's built-in assistant. They are not Codex or Claude skills, and they are not arbitrary executable plugins. A YO!skill teaches YO!agent how to decide, phrase, monitor, or coordinate work; YOLOmux server code still owns the actual tools, auth checks, target resolution, risk checks, audit events, and pane sends.

For common user intents, expected YO!agent behavior, multi-agent handoff examples, and the cross-agent communication reliability ladder, see [`specs/YOAGENT_COMMON_INTENTS_AND_AGENT_COMMUNICATION.md`](specs/YOAGENT_COMMON_INTENTS_AND_AGENT_COMMUNICATION.md).

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

Required fields are `name` and a useful `description`. `name` must match `[a-z][a-z0-9-]{1,63}` and must match the YAML file stem for user-local files. `tools` can name only server-known capability labels: `read_activity`, `read_settings_catalog`, `read_product_capabilities`, `recommend_next_work`, `watch_session`, `watch_all_sessions`, `notify_user`, `read_skill_files`, `write_skill_file`, `delete_skill_file`, `write_settings_patch`, `preview_send_prompt`, `execute_confirmed_send`, and `summarize_sessions`.

## Examples

Create a roster wait-and-send job through YO!agent:

- `periodically monitor 1 2 3 4 and if they are all done, then send a /dyn-tps-report 1 2 3 4 EOD to session 1`

YO!agent parses this deterministic workflow before invoking a model. For flexible equivalent wording that does not match the local parser, its shared model-intent registry invokes the one matching built-in skill as an isolated no-tools planner with that skill's JSON Schema and the known session names. The server accepts only a schema-valid plan using known sessions, routes it through the normal job validation, fixes the ten-second quiet default, and always leaves the resulting model-derived job pending your confirmation. It persists the watch roster separately from the destination, waits until each listed Claude/Codex pane is idle or done with an empty composer for the stable quiet window, revalidates immediately before the one verified send, and fires once. It waits through work, questions, approvals, errors, disconnects, non-agent panes, and drafts; a removed tmux session fails the job visibly. The command is not rewritten, and YO!agent captures a target result only if you explicitly request it.

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

## Settings And Product-State Operator

YOLOmux ships a built-in `settings-operator` skill. It lets YO!agent answer Preferences and product-state questions from deterministic server data before calling Claude or Codex. The source of truth is the backend settings catalog, not the rendered Preferences DOM.

Useful prompts:

- `what is my tab width?`
- `what settings affect tabs?`
- `where is settings.yaml?`
- `show all notification settings`
- `set theme to light`
- `change background to white`
- `change color from green to orange`
- `change active color to blue`
- `change cursor color to yellow`
- `change tab width to 220`
- `add watched PR owner/repo#123`
- `what did I last work on?`
- `what can I do from here?`

Rules for settings skills:

- Read-only users may ask what settings are, what values are valid, where config lives, and what YOLOmux can do.
- Admin writes must use `write_settings_patch`, which routes through the server settings save path and normal validation; skills must not edit `~/.config/yolomux/settings.yaml` directly.
- Background/white/black requests map to the global Appearance theme: `change background to white` means `appearance.theme=light`; `change background to black` means `appearance.theme=dark`.
- Bare UI color changes such as `change color from green to orange` mean the Appearance `appearance.active_color` setting unless the user names cursor color, separator color, theme, or another specific color setting.
- The model may choose or explain a setting, but the server validates keys, values, ranges, list semantics, auth, and risky paths.
- If a model-backed answer is slow, use the response Details block for safe diagnostics: backend, response time in seconds, prompt size, resume/seed state, and fallback reason. Do not expose raw hidden chain-of-thought; if a backend returns `<think>` text, hide that raw block and report only that it was hidden.
- Normal explicit writes can happen immediately. Ambiguous settings, broad resets, prompt/format edits, YOLO rule path changes, share access changes, and path-sensitive list changes should ask for clarification or confirmation.
- Credential-heavy paths such as `.ssh`, `.gnupg`, `.aws`, token files, registry config, and values containing token/secret/password/API-key text must not be stored or displayed.
- YO!agent should keep Preferences work local to YO!agent. Do not ask target tmux agents to change YOLOmux Preferences unless the user is separately orchestrating a coding task.

## Managing Files

You can edit the files directly, or ask YO!agent to manage them:

- `list my YO!skills`
- `read skill local-status`
- `create skill local-status description: Ask idle agents for status and next steps.`
- `update skill local-status` with fenced YAML
- `delete skill local-status`

The file-management API is admin-only: `GET /api/yoagent/skill-files?kind=skill&name=local-status`, `POST /api/yoagent/skill-files/upsert`, and `POST /api/yoagent/skill-files/delete`. It validates names, canonical paths, YAML, and allowed tools before writing or deleting files.

## Coordination Rules

The detailed coordination spec lives in [`specs/YOAGENT_COMMON_INTENTS_AND_AGENT_COMMUNICATION.md`](specs/YOAGENT_COMMON_INTENTS_AND_AGENT_COMMUNICATION.md). The rules below are the short version that built-in and user-local skills must preserve.

### Transports

YO!agent skills describe intent; the server selects the transport. Existing visible Claude/Codex panes use `tmux-legacy`, a verified tmux paste-plus-Return fallback backed by `yolomux_lib/agent_tui.py` for pane capture, cursor/composer facts, prompt-state preflight, clear, paste-submit, and post-send checks. Managed providers such as Claude SDK, Claude Channels, Codex SDK, Codex app-server, Codex MCP server, `codex-exec`, and Claude stream-json are separate transports with different guarantees around result events, auth, sandbox, opt-in session identity, and completion semantics. A skill should request `preview_send_prompt`, `execute_confirmed_send`, or a watch tool instead of telling an agent to use tmux or contact another session directly.

### Perspectives

YO!agent keeps two perspectives separate. The user talks to YO!agent about routing, scheduling, and coordination; each target agent receives only the task/question from that target agent's own point of view. For `ask agent 1 to <do ...>`, the prompt sent to agent `1` is `<do ...>`, not `ask agent 1 to <do ...>`. For `ask session 1 what it has done today`, the prompt sent to session `1` is `what have you done today?`, while YO!agent may still tell the user it is asking tmux session `1`.

YO!agent is the default orchestrator. For multi-session handoffs, YO!agent asks the first session, waits for the response, derives the next prompt itself, verifies the next session is accepting an AI prompt, then sends a clean task or question. Target sessions do not know about each other unless the user explicitly asks for disclosure or relay/chaining.

Direct relay/chaining between target agents is rare. If the user explicitly requests it, YO!agent must pass concrete relay instructions instead of leaving the target agent to infer routing.
