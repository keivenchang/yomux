<!-- SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved. -->
<!-- SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 -->

# Codex And Claude Text Clients

`tools/codex.py` and `tools/claude.py` are text-first prototype clients for driving Codex and Claude without scraping their terminal UIs. They share the terminal/readline/prompt/output/metrics layer in `tools/text_client_common.py`, but they talk to different upstream backends.

## Shared Parent

Both clients inherit from `TextClientBase` in `tools/text_client_common.py`.

Shared behavior:

- Prompt format: `model[effort] cwd›`
- Readline bindings: Ctrl-A start, Ctrl-E end, Ctrl-K kill to end, Ctrl-U kill line, Ctrl-W kill word, Ctrl-Y yank, Ctrl-P/Ctrl-N history, Ctrl-R history search, Alt-B/Alt-F move word, Alt-D kill word, Tab completes slash commands.
- Prefixed gray output: codex.py reasoning (aka thinking), claude.py thinking (aka reasoning), tool events, thread/session hints, and metrics use the shared color helpers.
- Shared metrics: TTFT, submit-to-first-token, first reasoning (aka thinking) or thinking (aka reasoning) event, first tool, total turn time, ISL, OSL, token/sec, answer chars/sec, tool counts, tool duration, approval counts, and event counts.
- Shared config helpers: bool parsing, TOML-ish `key=value` parsing, config display, shell-safe resume command formatting.
- Shared same-intent metadata: `OutputTerminology`, `ClientConfigKeys`, `ClientPermissionDefaults`, and `CLIENT_INTENT_ROWS` in `tools/text_client_common.py` own the terminology map, config-key map, prefix labels, and permissive defaults used by both clients.
- Shared terminal color selection: aux text and prompt colors are selected once in the base class.

## Background Color Handling

The clients auto-detect light vs dark terminal backgrounds and adjust prompt/auxiliary text intensity from the shared base class. `TEXT_CLIENT_BACKGROUND=light|dark` wins, then `YOLOMUX_TEXT_CLIENT_BACKGROUND=light|dark`, then an OSC 11 xterm-compatible background query, then `COLORFGBG` if the terminal exposes it. If none of those work, the client reports `Terminal bg: unknown (fallback)` and uses the dark-background palette.

OSC 11 is only attempted when stdin and stdout are real TTYs. The query runs against `/dev/tty`, restores terminal settings immediately, and has a short timeout so non-responsive terminals fall back cleanly. To force the palette, run one of:

```bash
TEXT_CLIENT_BACKGROUND=light python3 tools/codex.py -C .
TEXT_CLIENT_BACKGROUND=dark python3 tools/claude.py -C .
```

## Backend Differences

| Client | Backend | Conversation id | Resume data | Stream shape |
| --- | --- | --- | --- | --- |
| `codex.py` | `codex app-server --listen stdio://` JSON-RPC | Codex thread id | JSONL session under `~/.codex/sessions/...` when the app-server reports one | JSON-RPC notifications and requests |
| `claude.py` | `claude -p --verbose --output-format stream-json --include-partial-messages` | Claude session id | Claude CLI-managed session id | One subprocess per turn with stream-json events |

`codex.py` keeps a persistent app-server process for the client lifetime. `claude.py` starts a Claude subprocess per turn and passes `--resume` or `--continue` as needed.

## Terminology Map

| User concept | `codex.py` / Codex wording | `claude.py` / Claude wording | Same idea? | Notes |
| --- | --- | --- | --- | --- |
| Hidden model work | Reasoning (aka Thinking) | Thinking (aka Reasoning) | Yes | Both are internal progress/analysis streams. The prototypes expose only what the upstream stream emits. |
| Effort level | `model_reasoning_effort`, `/effort`, `-c model_reasoning_effort=high` | `--effort`, `/effort`, `/config effort=high` | Yes | codex.py maps `/effort` to Codex reasoning effort; claude.py passes `--effort` to Claude. |
| Reasoning/thinking summary | `model_reasoning_summary`, `text_client.show_reasoning_summary` | No separate summary setting; use `text_client.show_thinking` for thinking output | Similar | Codex has summary controls; Claude stream-json emits thinking deltas when available and enabled. |
| Raw reasoning/thinking | `text_client.show_raw_reasoning=true` | `text_client.show_thinking=true` | Similar | codex.py separates summary and raw reasoning; claude.py has one thinking output toggle. |
| Output prefix | <code>reasoning&#124; ...</code> | <code>thinking&#124; ...</code> | Yes | Prefixes intentionally match each upstream product's native term. |
| Tool output | <code>tool&#124; ...</code>, `text_client.show_tool_output` | <code>tool&#124; ...</code>, `text_client.show_tool_output` | Yes | Both clients print tool events in gray through the shared base class. |
| Model selector | `-m`, `/model`, `/config model=...` | `-m`, `/model`, `/config model=...` | Yes | Values are passed to different upstream model systems. |
| Resume id | Codex thread id, `resume <thread-id>`, `text_client.thread_id` | Claude session id, `--resume <session-id>`, `text_client.session_id` | Same purpose | The id formats and transcript locations differ. |
| Permissive mode | `--dangerously-bypass-approvals-and-sandbox`, `--dangerously-bypass-hook-trust`, `sandbox=danger-full-access`, `approval_policy=never`, `bypass_hook_trust=true` | `--dangerously-skip-permissions`, `permission_mode=bypassPermissions` | Same intent | Both prototypes default to this permissive mode. Codex separates approval/sandbox bypass from hook-trust bypass; Claude has one broad permission bypass. |
| Approval handling | `approval_policy`, `text_client.approval_mode` | `permission_mode` | Similar | codex.py can also auto-answer app-server approval requests with `text_client.approval_mode=accept`. claude.py delegates permission handling to Claude CLI. |
| Web access | `--search`, `web_search=live` for Codex web search | `WebFetch` / web tools through Claude tool permissions | Similar | Different upstream tool names and permission controls. |
| Diagnostics | `text_client.debug_json`, `text_client.raw_output`, `/raw` | `text_client.raw_json`, `/raw` | Similar | Both are prototype diagnostics, not the main answer stream. |
| Metrics | `text_client.show_metrics`, `/metrics` | `text_client.show_metrics`, `/metrics` | Yes | Shared metric names and formatting come from `TextClientBase`. |

## Common Slash Commands

Both clients support these prototype commands:

- `/status`: show active model, effort, directory, session/thread id, output toggles, metrics toggle, and terminal background detection.
- `/help`: show client-supported slash commands and key settings.
- `/config key=value`: show or change runtime settings inside the REPL.
- `/model [model]`: show or change model.
- `/effort [level]`: show or change effort.
- `/resume <id>`: continue a Codex thread id or Claude session id.
- `/usage`: show token/cost/metrics information from the last turn when available.
- `/metrics [on|off|last]`: toggle metrics output or print the last metrics block.
- `/raw [on|off]`: toggle raw diagnostic output for the client backend.
- `/context`: show cwd, model/effort, session/thread id, backend-specific permission state, output toggles, and last-turn metrics when available.
- `/clear`: clear conversation context so the next prompt starts a new Claude session or Codex thread.
- `/cls`: clear the terminal screen only.
- `/quit`: exit and print the resume command when a session/thread exists.

Some commands are compatibility commands. When a command exists in one real client but not the other, the prototype keeps the command for muscle memory and prints a gray note explaining that it is clone-provided compatibility behavior.

## CLI Startup And Slash Command Audit

Audited against local Claude Code `2.1.183` and Codex CLI `0.141.0` on 2026-06-20. This audit intentionally ignores top-level process subcommands such as `codex exec`, `codex review`, `codex doctor`, `claude doctor`, `claude auth`, and `claude update`. Those are separate CLI entrypoints, not the interactive session behavior these text clients are trying to mimic. The relevant surfaces are startup flags for launching an interactive session and in-session slash commands like `/status`, `/model`, and `/help`.

Rule for `claude.py` and `codex.py`: mimic interactive Claude/Codex CLI muscle memory where it maps to the text-client session loop. Implement slash commands that control the wrapper runtime, keep compatibility stubs for familiar real-client slash commands that are not implemented yet, and show a gray note when a command is clone-provided for parity rather than native to that upstream product. Do not spend implementation work on top-level process subcommands unless the user explicitly asks for a launcher/proxy mode.

| Startup flag family | Current `claude.py` support | Current `codex.py` support | Required action |
| --- | --- | --- | --- |
| Initial prompt | Positional prompt starts a Claude turn through `claude -p --output-format stream-json` | Positional prompt starts a Codex turn through `codex app-server` | Keep. This is the main wrapper entrypoint. |
| Working directory | `-C/--cd` is a wrapper convenience, `--add-dir` maps to real Claude | `-C/--cd` and `--add-dir` match real Codex startup flags | Keep `-C` on both clients for parity, even though real Claude does not expose `-C`. |
| Model and effort | `--model`, `--effort`, `/model`, and `/effort` map to Claude session settings | `--model`, `-c model_reasoning_effort=...`, `/model`, and `/effort` map to Codex model/reasoning settings | Keep `/effort` in codex.py as clone-provided compatibility with Claude-style muscle memory, and keep the gray note that real Codex uses config terminology. |
| Permissions and sandboxing | `--permission-mode`, `--dangerously-skip-permissions`, `--allowedTools`, `--disallowedTools`, and `--tools` map to Claude | `--sandbox`, `--ask-for-approval`, `--dangerously-bypass-approvals-and-sandbox`, `--dangerously-bypass-hook-trust`, and `text_client.approval_mode` map to Codex/app-server behavior | Keep permissive defaults and make `/status` show the effective mode in both clients. |
| Resume and identity | `--resume`, `--continue`, `--session-id`, and `/resume` map to Claude session ids | `/resume`, `resume <thread-id>`, and `text_client.thread_id` map to Codex thread ids | Keep explicit-id resume. Picker-style resume can stay out of scope until requested. |
| Output controls | `--show-status`, `--hide-tool-output`, `--show-thinking`, `--hide-thinking`, `--show-metrics`, `--hide-metrics`, and `--raw-json` control wrapper rendering of Claude stream-json | `-c text_client.show_*`, `/raw`, `/metrics`, and reasoning summary config control wrapper rendering of Codex app-server events | Keep shared rendering controls and shared gray output. |
| Real interactive-only flags | Real Claude has interactive startup flags such as `--worktree`, `--tmux`, `--remote-control`, `--chrome`, `--ide`, `--name`, `--agent`, `--agents`, `--safe-mode`, and `--bare` that claude.py does not expose | Real Codex flags are mostly covered; compatibility-only flags like `--remote`, `--image`, `--oss`, `--local-provider`, `--profile`, and `--no-alt-screen` are accepted but not fully implemented | Add a wrapper flag only when it changes text-client behavior. Otherwise document it as accepted compatibility or unsupported. |
| Real print/stream flags | claude.py fixes `-p --output-format stream-json --include-partial-messages` internally and exposes only the settings that make sense for stdout rendering | codex.py uses app-server JSON-RPC instead of Codex TUI output flags | Keep internals hidden unless a user-facing flag has a clean mapping. |

| Slash command family | Current `claude.py` support | Current `codex.py` support | Required action |
| --- | --- | --- | --- |
| Core shared commands | `/help`, `/status`, `/config`, `/model`, `/effort`, `/resume`, `/usage`, `/metrics`, `/raw`, `/quit` | `/help`, `/status`, `/config`, `/model`, `/effort`, `/resume`, `/usage`, `/metrics`, `/raw`, `/quit` | Keep implemented in the shared REPL where possible. |
| Permission commands | `/permission`, `/permission-mode`, and `/permissions` update or show Claude permission mode | `/permissions` shows Codex approval/sandbox settings; `/permission` and `/permission-mode` are compatibility aliases for the same handler | Keep all three routed through the shared slash-command registry so help/completion cannot drift. |
| Hidden work output | `/thinking` toggles Claude thinking output (aka reasoning); `/reasoning` is a Codex-style compatibility alias | `/reasoning` controls Codex reasoning (aka thinking) summary/raw visibility; `/thinking` is a Claude-style compatibility alias | Keep product wording explicit in help/status and print a gray note for compatibility aliases. |
| Convenience commands | `/context`, `/clear`, and `/cls` are implemented in claude.py | `/context`, `/clear`, `/cls`, and `/fast` are implemented in codex.py | `/clear` clears conversation context; `/cls` is terminal-only clearing. |
| Claude recognized but stubbed commands | `/agents`, `/compact`, `/goal`, `/heapdump`, `/init`, `/insights`, `/reload-skills`, `/review`, `/security-review`, `/team-onboarding` | Not applicable | Keep stubs only if they match real Claude slash-command muscle memory; implement later only when the text client can own the behavior. |
| Codex recognized but stubbed commands | Not applicable | `/app`, `/apps`, `/archive`, `/compact`, `/copy`, `/delete`, `/diff`, `/experimental`, `/feedback`, `/fork`, `/goal`, `/ide`, `/import`, `/init`, `/keymap`, `/mcp`, `/memories`, `/personality`, `/plugins`, `/ps`, `/rename`, `/review`, `/sandbox-add-read-dir`, `/side`, `/skills`, `/statusline`, `/stop` | Keep stubs only if they match real Codex slash-command muscle memory; implement later only when the text client can own the behavior. |
| Tool calls, approvals, file changes, and web/search calls | Streamed from Claude stream-json and shown as `tool| ...` | Streamed from Codex app-server JSON-RPC and shown as `tool| ...` | These are model/session events, not slash commands. Keep parsing and rendering them through shared output helpers. |

Recommended implementation order if more parity is needed: first add Ctrl-C guard behavior to the shared REPL, then capture real Claude/Codex slash-command `/help` output into fixtures, then compare wrapper slash-command help/status output against those fixtures, then implement the highest-value missing slash commands through shared parent logic.

## Codex Flags And Settings

`codex.py` accepts the Codex-like flags it can map:

- `-m, --model MODEL`
- `-c, --config key=value`
- `--enable FEATURE`
- `--disable FEATURE`
- `--remote ADDR`
- `--remote-auth-token-env TOKEN_VAR`
- `--strict-config`
- `-i, --image FILE`
- `--oss`
- `--local-provider OSS_PROVIDER`
- `-p, --profile CONFIG_PROFILE_V2`
- `-s, --sandbox read-only|workspace-write|danger-full-access` (default: `danger-full-access`)
- `--dangerously-bypass-approvals-and-sandbox`
- `--dangerously-bypass-hook-trust` (default: enabled in this client through `bypass_hook_trust=true`)
- `-C, --cd DIR`
- `--add-dir DIR`
- `-a, --ask-for-approval untrusted|on-failure|on-request|never` (default: `never`)
- `--search`
- `--no-alt-screen`

Useful Codex `-c` keys:

- `model_reasoning_effort=minimal|low|medium|high|xhigh` for codex.py reasoning (aka thinking) effort.
- `model_reasoning_summary=none|auto|concise|detailed` for codex.py reasoning (aka thinking) summaries; `summary` aliases to `concise`.
- `service_tier=fast`
- `approval_policy=untrusted|on-failure|on-request|never` (default: `never`)
- `sandbox=read-only|workspace-write|danger-full-access` (default: `danger-full-access`)
- `bypass_hook_trust=true|false` (default: `true`)
- `web_search=live`
- `text_client.show_reasoning_summary=true|false` for codex.py reasoning (aka thinking) summaries.
- `text_client.show_raw_reasoning=true|false` for raw codex.py reasoning (aka thinking).
- `text_client.show_tool_output=true|false`
- `text_client.show_metrics=true|false`
- `text_client.debug_json=true|false`
- `text_client.thread_id=<id>`
- `text_client.timeout=<seconds>`
- `text_client.approval_mode=prompt|accept|accept-session|deny|abort` (default: `accept`)

Codex `/help` queries the local app-server for the model catalog and includes hidden models when available. Codex `/status` includes the absolute session JSONL path after a thread has started and the app-server reports the path.

Codex reasoning controls are available as `/reasoning [on|off|summary|raw|none|auto|concise|detailed]`. `/thinking` is accepted as a Claude-style compatibility alias for the same settings and prints a gray compatibility note.

## Claude Flags And Settings

`claude.py` accepts the Claude-like flags it can map:

- `-m, --model MODEL`
- `--effort low|medium|high|xhigh|max`
- `-C, --cd DIR`
- `--add-dir DIR`
- `--allowedTools, --allowed-tools TOOLS`
- `--disallowedTools, --disallowed-tools TOOLS`
- `--tools TOOLS`
- `--permission-mode acceptEdits|auto|bypassPermissions|default|dontAsk|plan` (default: `bypassPermissions`)
- `--dangerously-skip-permissions` (alias for `--permission-mode bypassPermissions`)
- `-r, --resume SESSION_ID`
- `-c, --continue`
- `--session-id UUID`
- `--system-prompt PROMPT`
- `--append-system-prompt PROMPT`
- `--max-budget-usd USD`
- `--show-status`
- `--hide-tool-output`
- `--show-thinking` for claude.py thinking (aka reasoning) output.
- `--hide-thinking` for claude.py thinking (aka reasoning) output.
- `--show-metrics`
- `--hide-metrics`
- `--raw-json`
- `--timeout SECONDS`

Useful Claude `/config` keys:

- `model=<model>`
- `effort=low|medium|high|xhigh|max`
- `permission_mode=acceptEdits|auto|bypassPermissions|default|dontAsk|plan` (default: `bypassPermissions`)
- `text_client.show_tool_output=true|false`
- `text_client.show_metrics=true|false`
- `text_client.show_thinking=true|false` for claude.py thinking (aka reasoning).
- `text_client.show_status=true|false`
- `text_client.raw_json=true|false`
- `text_client.timeout=<seconds>`
- `text_client.session_id=<id>`

Claude model aliases are passed through to the real Claude CLI. This prototype does not currently query a live Claude model catalog.

Claude thinking controls are available as `/thinking [on|off]`. `/reasoning [on|off]` is accepted as a Codex-style compatibility alias for the same setting and prints a gray compatibility note.

## Output Labels

codex.py uses `reasoning| ...` for Codex reasoning (aka thinking), plus `tool| ...` and `metrics| ...` for other non-answer output. claude.py uses `thinking| ...` for Claude thinking (aka reasoning), plus `tool| ...` and `metrics| ...`. Normal assistant text is printed directly to stdout.

## Resume Commands

Both clients print a shell-safe resume command on exit when a thread/session id exists. The resume command preserves current settings such as model, effort, working directory, output toggles, metrics toggles, timeout, and backend-specific config where possible.
