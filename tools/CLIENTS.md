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

## Mock Agent Fixture Replay

`tools/claude.py --mock` and `tools/codex.py --mock` run the TUI mocks for detector, auto-approve, and browser tests. The mock implementations live in the real client entry points plus shared code in `tools/mock_agent_common.py`; the old top-level `mock/` package is intentionally gone. The mocks replay the real and synthetic prompt corpus from `tests/fixtures/prompt_corpus/` so tests exercise current Claude Code and Codex CLI chrome without launching the real clients for every case.

- `mock list all`: print replayable prompt-corpus cases for the current agent, plus shared and idle fixtures.
- `fixture <case>`: clear the pane, render that fixture, bottom-align short captures in the current tmux pane, and freeze the process so `tmux capture-pane` sees the prompt exactly as a live client would.
- Case names accept the fixture scenario, fixture id, inventory id, file stem, and agent-prefixed forms such as `claude_ask_user_question` or `codex_shell_sleep_10_3_option`.
- Plain `mock <case>` returns to the live composer for idle/history fixtures, but active working fixtures such as Codex `goal_active` occupy and freeze the whole pane so a second live composer is not appended below the captured status row. Plain live `mock <case>` re-renders from the recorded fixture width to the current pane width and stretches or shrinks separator-only rows and box-border rows that exactly hit the recorded fixture width. It does not merge menu options, prompts, footers, command/tool rows, or fresh assistant rows. `fixture <case>` preserves recorded rows only when the pane is at least as wide as the source fixture; narrower panes re-render from the wide source to the current width.
- The shared live composer must treat terminal size as state. When tmux, the pane, or the terminal changes width or height while `claude.py` or `codex.py` is waiting for input, the client re-renders the composer/status footer at the new bottom rows, resets the scroll region above it, and clears stale Claude startup-header rows before drawing the header at its new position.
- `yesno [N]`, `ask N`, `sleep N`, and shell commands still drive interactive permission prompts; use `fixture` for corpus parity and the older commands for auto-approve flow tests that need keyboard interaction.
- `--dump-fixtures` dumps this client's parser-relevant fixture corpus to stdout and exits without starting a real upstream agent. Each block has `===== BEGIN FIXTURE N/T: <filename> =====`, metadata rows for `agent`, `case`, `outcome`, `path`, and `cursor`, a `----- capture (source <W>x<H>; rendered <W>x<H>; cursor marked) -----` separator, the captured terminal text rendered at the fixture source width, a `^ cursor` marker row under the cursor's rendered row when cursor metadata is available and visible, then `===== END FIXTURE: <filename> =====`. Cursor coordinates are 0-based in the source fixture; `shown=x=... y=...` is relative to the dumped rendered text after dump rendering and cropping. Separator-only rows are rebuilt as one rendered-width row, not wrapped into a fake second rule row. Metadata rows, fixture names, and paths are full logical lines; the dump must not insert a narrow non-TTY fallback. Codex dumps Codex-owned fixtures plus shared `generic`/`unknown` detector fixtures; Claude dumps Claude-owned fixtures plus shared `generic`/`unknown` detector fixtures. Idle negative fixtures are included because the dump is meant to show everything that can flow through the parser, even cases that should not trigger attention/YOLO/RUN.

Some prompt-corpus case names are intentionally product-specific. The corpus records semantic parity groups in `tests/fixtures/prompt_corpus/inventory.yaml`, and every promoted real capture in `tests/fixtures/prompt_corpus/captures/inventory.yaml` must be in one of those groups. For example, Claude `working_visible_counter` and Codex `working_command_counter` are both the `visible-working-counter` group and both must classify as `RUN`, even though their capture names came from different live repros. Same-name parity is required only when the product state is actually the same, such as `idle_empty_prompt` and `goal_active`; approval prompts are grouped by detector contract because Claude plan/file/tool permissions and Codex shell/escalation/MCP approvals are different product surfaces. One-sided real captures need an explicit product-specific parity group, for example Claude's interrupted `What should Claude do instead?` prompt has no matching Codex question surface in the current corpus. Synthetic cases that should eventually be replaced by live evidence are tracked separately as `live_capture_targets`; fill those by capturing the named upstream client state, not by fabricating same-name fixtures for the other client.

## Terminology Map

| User concept | `codex.py` / Codex wording | `claude.py` / Claude wording | Same idea? | Notes |
| --- | --- | --- | --- | --- |
| Hidden model work | Reasoning (aka Thinking) | Thinking (aka Reasoning) | Yes | Both are internal progress/analysis streams. The prototypes expose only what the upstream stream emits. |
| Effort level | `--effort`, `model_reasoning_effort`, `/effort`, `-c model_reasoning_effort=high` | `--effort`, `/effort`, `/config effort=high` | Yes | codex.py maps `--effort` and `/effort` to Codex reasoning effort; claude.py passes `--effort` to Claude. |
| Reasoning/thinking summary | `model_reasoning_summary`, `text_client.show_reasoning_summary` | No separate summary setting; use `text_client.show_thinking` for thinking output | Similar | Codex has summary controls; Claude stream-json emits thinking deltas when available and enabled. |
| Raw reasoning/thinking | `text_client.show_raw_reasoning=true` | `text_client.show_thinking=true` | Similar | codex.py separates summary and raw reasoning; claude.py has one thinking output toggle. |
| Output prefix | <code>reasoning&#124; ...</code> | <code>thinking&#124; ...</code> | Yes | Prefixes intentionally match each upstream product's native term. |
| Tool output | <code>tool&#124; ...</code>, `text_client.show_tool_output` | <code>tool&#124; ...</code>, `text_client.show_tool_output` | Yes | Both clients print tool events in gray through the shared base class. |
| Assistant Markdown | TTY answer text renders common Markdown spans such as `**bold**`, `*italic*`, and inline code; redirected output stays plain | Same | Yes | Rendering applies only to assistant-visible answer text. Thinking/reasoning and tool streams keep their prefixed diagnostic formatting. |
| Model selector | `-m`, `/model`, `/config model=...` | `-m`, `/model`, `/config model=...` | Yes | Values are passed to different upstream model systems. |
| Resume id | Codex thread id, `resume <thread-id>`, `text_client.thread_id` | Claude session id, `--resume <session-id>`, `text_client.session_id` | Same purpose | The id formats and transcript locations differ. |
| Permissive mode | `--dangerously-bypass-approvals-and-sandbox`, `sandbox=danger-full-access`, `approval_policy=never`; optional `--dangerously-bypass-hook-trust` / `bypass_hook_trust=true` | `--dangerously-skip-permissions`, `permission_mode=bypassPermissions` | Same intent | Codex keeps hook-trust bypass explicit because upstream Codex only warns when that flag/config is set; Claude has one broad permission bypass. |
| Approval handling | `approval_policy`, `text_client.approval_mode` | `permission_mode` | Similar | codex.py can also auto-answer app-server approval requests with `text_client.approval_mode=accept`. claude.py delegates permission handling to Claude CLI. |
| Web access | `--search`, `web_search=live` for Codex web search | `WebFetch` / web tools through Claude tool permissions | Similar | Different upstream tool names and permission controls. |
| Diagnostics | `--raw-json`, `text_client.debug_json`, `text_client.raw_output`, `/raw` | `--raw-json`, `text_client.raw_json`, `/raw` | Similar | Both are prototype diagnostics, not the main answer stream. |
| Metrics | `--show-metrics`, `--hide-metrics`, `text_client.show_metrics`, `/metrics` | `--show-metrics`, `--hide-metrics`, `text_client.show_metrics`, `/metrics` | Yes | Shared metric names and formatting come from `TextClientBase`. |

## Common Slash Commands

Both clients support these prototype commands:

- `/status`: show active model, effort, directory, session/thread id, output toggles, metrics toggle, and terminal background detection.
- `/help`: show client-supported slash commands and key settings.
- `/config key=value`: show or change runtime settings inside the REPL.
- `/model [model]`: with no argument, show the current model and available model options; with an argument, change model. Codex also accepts `/model <model> <effort>`.
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
| Model and effort | `--model`, `--effort`, `/model`, and `/effort` map to Claude session settings | `--model`, `--effort`, `-c model_reasoning_effort=...`, `/model`, and `/effort` map to Codex model/reasoning settings | Keep `--effort` and `/effort` in codex.py as clone-provided compatibility with Claude-style muscle memory, and keep the gray note that real Codex uses config terminology. |
| Permissions and sandboxing | `--permission-mode`, `--dangerously-skip-permissions`, `--allowedTools`, `--disallowedTools`, and `--tools` map to Claude | `--sandbox`, `--ask-for-approval`, `--dangerously-bypass-approvals-and-sandbox`, `--dangerously-bypass-hook-trust`, and `text_client.approval_mode` map to Codex/app-server behavior | Keep permissive defaults and make `/status` show the effective mode in both clients. |
| Resume and identity | `--resume`, `--continue`, `--session-id`, and `/resume` map to Claude session ids | `/resume`, `resume <thread-id>`, and `text_client.thread_id` map to Codex thread ids | Keep explicit-id resume. Picker-style resume can stay out of scope until requested. |
| Output controls | `--show-status`, `--hide-tool-output`, `--show-thinking`, `--hide-thinking`, `--show-metrics`, `--hide-metrics`, and `--raw-json` control wrapper rendering of Claude stream-json | `--hide-tool-output`, `--show-thinking`, `--hide-thinking`, `--show-metrics`, `--hide-metrics`, `--raw-json`, `-c text_client.show_*`, `/raw`, `/metrics`, and reasoning summary config control wrapper rendering of Codex app-server events | Keep shared rendering controls and shared gray output. |
| Real interactive-only flags | Real Claude has interactive startup flags such as `--worktree`, `--tmux`, `--remote-control`, `--chrome`, `--ide`, `--name`, `--agent`, `--agents`, `--safe-mode`, and `--bare` that claude.py does not expose | Real Codex flags are mostly covered; compatibility-only flags like `--remote`, `--image`, `--oss`, `--local-provider`, `--profile`, and `--no-alt-screen` are accepted but not fully implemented | Add a wrapper flag only when it changes text-client behavior. Otherwise document it as accepted compatibility or unsupported. |
| Real print/stream flags | claude.py fixes `-p --output-format stream-json --include-partial-messages` internally and exposes only the settings that make sense for stdout rendering | codex.py uses app-server JSON-RPC instead of Codex TUI output flags | Keep internals hidden unless a user-facing flag has a clean mapping. |

| Slash command family | Current `claude.py` support | Current `codex.py` support | Required action |
| --- | --- | --- | --- |
| Core shared commands | `/help`, `/status`, `/config`, `/model`, `/effort`, `/resume`, `/usage`, `/metrics`, `/raw`, `/quit` | `/help`, `/status`, `/config`, `/model`, `/effort`, `/resume`, `/usage`, `/metrics`, `/raw`, `/quit` | Keep implemented in the shared REPL where possible. |
| Permission commands | `/permission`, `/permission-mode`, and `/permissions` update or show Claude permission mode | `/permissions` shows Codex approval/sandbox settings; `/permission` and `/permission-mode` are compatibility aliases for the same handler | Route all three through the shared slash-command registry so help/completion cannot drift; add codex aliases only if they map to the same permission summary/update logic. |
| Hidden work output | `/thinking` toggles Claude thinking output (aka reasoning); `/reasoning` is a Codex-style compatibility alias | `/reasoning` controls Codex reasoning summary/raw visibility (via `/config model_reasoning_summary=...`, `/config text_client.show_reasoning_summary=...`, etc.); `/thinking` is a Claude-style compatibility alias | Keep product wording explicit in help/status; print a gray note for compatibility aliases. |
| Convenience commands | `/context`, `/clear`, and `/cls` are implemented in claude.py | `/context`, `/clear`, `/cls`, and `/fast` are implemented in codex.py | `/clear` clears conversation context; `/cls` is terminal-only clearing; move any same-intent behavior into the shared parent before adding more. |
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
- `-m, --model MODEL`
- `--effort minimal|low|medium|high|xhigh`
- `--oss`
- `--local-provider OSS_PROVIDER`
- `-p, --profile CONFIG_PROFILE_V2`
- `-s, --sandbox read-only|workspace-write|danger-full-access` (default: `danger-full-access`)
- `--dangerously-bypass-approvals-and-sandbox`
- `--dangerously-bypass-hook-trust`
- `-C, --cd DIR`
- `--add-dir DIR`
- `-a, --ask-for-approval untrusted|on-failure|on-request|never` (default: `never`)
- `--search`
- `--no-alt-screen`
- `--hide-tool-output`
- `--show-thinking` (default)
- `--hide-thinking`
- `--show-metrics`
- `--hide-metrics` (default)
- `--raw-json`
- `--timeout SECONDS`
- `--mock`
- `--dump-fixtures`

Useful Codex `-c` keys:

- `model=<model>` to change the model; launch shortcut: `-m <model>`. Default: `gpt-5.4-mini`.
- `model_reasoning_effort=minimal|low|medium|high|xhigh` for codex.py reasoning (aka thinking) effort; launch shortcut: `--effort`. Default: `medium`.
- `model_reasoning_summary=none|auto|concise|detailed` for codex.py reasoning (aka thinking) summaries; `summary` aliases to `concise`.
- `service_tier=fast`
- `approval_policy=untrusted|on-failure|on-request|never` (default: `never`)
- `sandbox=read-only|workspace-write|danger-full-access` (default: `danger-full-access`)
- `bypass_hook_trust=true|false` (default: `false`)
- `web_search=live`
- `text_client.show_reasoning_summary=true|false` for codex.py reasoning (aka thinking) summaries.
- `text_client.show_raw_reasoning=true|false` for raw codex.py reasoning (aka thinking).
- `text_client.show_tool_output=true|false`
- `text_client.show_metrics=true|false`
- `text_client.debug_json=true|false`
- `text_client.thread_id=<id>`
- `text_client.timeout=<seconds>`
- `text_client.approval_mode=prompt|accept|accept-session|deny|abort` (default: `accept`)

`codex.py --help` and `claude.py --help` use the same high-level scan path after the option list: `Models:`, `Common config settings:`, and `Inside the REPL:`. Codex `/help` queries the local app-server for the model catalog and includes hidden models when available. Codex `/status` includes the absolute session JSONL path after a thread has started and the app-server reports the path.

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
- `--mock`
- `--dump-fixtures`

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

Outside Codex TTY mode, both clients print a shell-safe resume command on exit when a thread/session id exists. Codex TTY mode follows the native terminal surface and suppresses internal `[thread]`/`[resume]` diagnostics. The resume command preserves current settings such as model, effort, working directory, output toggles, metrics toggles, timeout, and backend-specific config where possible.
