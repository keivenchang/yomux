# YO!agent

YO!agent answers product questions, watches sessions, sends reviewed prompts, and coordinates handoffs. It currently requires the deprecated Python server, which is an explicit temporary fallback planned for removal around late October 2026. Go status and token collection do not provide YO!agent actions; check `GO_MIGRATION.md` before relying on backend-specific features.

## Files

- Built-in skills: `yolomux_lib/yoagent/builtin_skills/*.yaml`.
- Built-in context: `yolomux_lib/yoagent/builtin_context/*.md`.
- User skills: `~/.config/yolomux/skills.d/*.yaml`.
- User context: `~/.config/yolomux/context.d/*.md`.

User skills replace built-ins with the same name. Set `enabled: false` to disable a skill. Skills describe workflows and allowed YO!agent tools; they do not run shell commands.

## Safety and behavior

The server owns session lookup, authorization, risk checks, prompt preview, confirmation, sends, watches, and audit events. Model-generated plans are validated against known sessions and allowed tools. A model-proposed send always waits for user confirmation.

Wait-and-send jobs watch an explicit roster, require a stable quiet period, recheck the destination, and send once. Recurring jobs have bounded intervals, send counts, and lifetimes; they can be cancelled and stop when their target disappears or a send fails. YO!agent does not send a prompt twice to recover from an uncertain delivery.

Settings changes use the settings catalog and validated settings-write API. Skills must not edit `settings.yaml` directly or read credential files. Broad resets, ambiguous changes, and path-sensitive changes require confirmation.

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

The server rejects unknown tool names and invalid skill files. File management is available only through the authenticated YO!agent skill-file API.
