<!-- SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved. -->
<!-- SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 -->

# Codex And Claude Text Clients

`tools/codex.py` and `tools/claude.py` are text-first prototype clients for driving Codex and Claude without scraping their terminal UIs. They share the terminal/readline/prompt/output/metrics layer in `tools/text_client_common.py`, but they talk to different upstream backends.

## Shared Parent

Both clients inherit from `TextClientBase` in `tools/text_client_common.py`.

Shared behavior:

- Prompt format: `model[effort] cwd›`
- Readline bindings: Ctrl-A start, Ctrl-E end, Ctrl-K kill to end, Ctrl-U kill line, Ctrl-W kill word, Ctrl-Y yank, Ctrl-P/Ctrl-N history, Ctrl-R history search, Alt-B/Alt-F move word, Alt-D kill word, Tab completes slash commands.
- Prefixed gray output: reasoning/thinking, tool events, thread/session hints, and metrics use the shared color helpers.
- Shared metrics: TTFT, submit-to-first-token, first reasoning, first tool, total turn time, ISL, OSL, token/sec, answer chars/sec, tool counts, tool duration, approval counts, and event counts.
- Shared config helpers: bool parsing, TOML-ish `key=value` parsing, config display, shell-safe resume command formatting.
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
- `/quit`: exit and print the resume command when a session/thread exists.

Some commands are compatibility commands. When a command exists in one real client but not the other, the prototype keeps the command for muscle memory and prints a gray note explaining that it is clone-provided compatibility behavior.

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
- `-s, --sandbox read-only|workspace-write|danger-full-access`
- `--dangerously-bypass-approvals-and-sandbox`
- `--dangerously-bypass-hook-trust`
- `-C, --cd DIR`
- `--add-dir DIR`
- `-a, --ask-for-approval untrusted|on-failure|on-request|never`
- `--search`
- `--no-alt-screen`

Useful Codex `-c` keys:

- `model_reasoning_effort=minimal|low|medium|high|xhigh`
- `model_reasoning_summary=none|auto|concise|detailed`; `summary` aliases to `concise`.
- `service_tier=fast`
- `approval_policy=untrusted|on-failure|on-request|never`
- `web_search=live`
- `text_client.show_reasoning_summary=true|false`
- `text_client.show_raw_reasoning=true|false`
- `text_client.show_tool_output=true|false`
- `text_client.show_metrics=true|false`
- `text_client.debug_json=true|false`
- `text_client.thread_id=<id>`
- `text_client.timeout=<seconds>`
- `text_client.approval_mode=prompt|accept|accept-session|deny|abort`

Codex `/help` queries the local app-server for the model catalog and includes hidden models when available. Codex `/status` includes the absolute session JSONL path after a thread has started and the app-server reports the path.

## Claude Flags And Settings

`claude.py` accepts the Claude-like flags it can map:

- `-m, --model MODEL`
- `--effort low|medium|high|xhigh|max`
- `-C, --cd DIR`
- `--add-dir DIR`
- `--allowedTools TOOLS`
- `--disallowedTools TOOLS`
- `--tools TOOLS`
- `--permission-mode acceptEdits|auto|bypassPermissions|default|dontAsk|plan`
- `-r, --resume SESSION_ID`
- `-c, --continue`
- `--session-id UUID`
- `--system-prompt PROMPT`
- `--append-system-prompt PROMPT`
- `--max-budget-usd USD`
- `--show-status`
- `--hide-tool-output`
- `--show-thinking`
- `--hide-thinking`
- `--show-metrics`
- `--hide-metrics`
- `--raw-json`
- `--timeout SECONDS`

Useful Claude `/config` keys:

- `model=<model>`
- `effort=low|medium|high|xhigh|max`
- `permission_mode=acceptEdits|auto|bypassPermissions|default|dontAsk|plan`
- `text_client.show_tool_output=true|false`
- `text_client.show_metrics=true|false`
- `text_client.show_thinking=true|false`
- `text_client.show_status=true|false`
- `text_client.raw_json=true|false`
- `text_client.timeout=<seconds>`
- `text_client.session_id=<id>`

Claude model aliases are passed through to the real Claude CLI. This prototype does not currently query a live Claude model catalog.

## Output Labels

Codex uses `reasoning| ...`, `tool| ...`, and `metrics| ...` for non-answer output. Claude uses `thinking| ...`, `tool| ...`, and `metrics| ...`. Normal assistant text is printed directly to stdout.

## Resume Commands

Both clients print a shell-safe resume command on exit when a thread/session id exists. The resume command preserves current settings such as model, effort, working directory, output toggles, metrics toggles, timeout, and backend-specific config where possible.
