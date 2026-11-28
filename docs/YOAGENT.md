# YO!agent

YO!agent is the built-in assistant that can read session activity, watch sessions, and send prompts to them. It runs inside the Go server and answers through the installed Claude or Codex CLI, using that CLI's existing login. It appears in the browser once its store has initialized. Its routes are listed in [Go backend status](../GO_MIGRATION.md#chat-and-agent).

## Files

| What | Where |
| --- | --- |
| Built-in skills | `yomux_lib/yoagent/builtin_skills/*.yaml` |
| Built-in context | `yomux_lib/yoagent/builtin_context/*.md` |
| Your skills | `~/.config/yomux/skills.d/*.yaml` (also `.yml`) |
| Your context | `~/.config/yomux/context.d/*.md` |

- A skill of yours replaces the built-in skill with the same name. Set `enabled: false` to turn a skill off.
- Skill files are managed through the authenticated YO!agent skill-file API. It keeps the exact filename and extension case. A bare name works only when exactly one file matches; use the full filename when several extensions share a name.

## Skill format

```yaml
name: local-status
kind: workflow
description: Ask an idle session what changed and what is blocked.
tools:
  - read_activity
  - preview_send_prompt
  - execute_confirmed_send
triggers:
  - ask session 1 for local status
confirmation: none
default_timeout_minutes: 30
```

A skill describes a workflow and the YO!agent tools it may use; it never runs shell commands. The allowed tools are `read_activity`, `read_settings_catalog`, `read_product_capabilities`, `recommend_next_work`, `watch_session`, `watch_all_sessions`, `notify_user`, `read_skill_files`, `write_skill_file`, `delete_skill_file`, `write_settings_patch`, `preview_send_prompt`, `execute_confirmed_send`, and `summarize_sessions`. The server rejects unknown tools and invalid skill files.

## Safety rules

- The server, not the model, does session lookup, authorization, risk checks, prompt preview, confirmation, sending, watches, and audit events. Model plans are checked against known sessions and allowed tools.
- A send proposed by the model always waits for your confirmation.
- Wait-and-send jobs watch an explicit list of sessions, wait for a stable quiet period, recheck the target, and send once.
- Recurring jobs have limits on interval, number of sends, and lifetime. You can cancel them, and they stop when the target disappears or a send fails.
- YO!agent never resends a prompt to recover from an uncertain delivery.
- Settings changes go through the settings catalog and the validated settings API. Skills must not edit `settings.yaml` directly or read credential files. Broad resets, ambiguous changes, and path-sensitive changes need confirmation.
