# Agent Prompts And Communication Spec

This file records the working knowledge YOLOmux uses to detect Claude and Codex state from terminal screens, transcripts, and structured approval channels. Treat the exact strings as versioned examples, not a permanent API. The durable contract is the state model, the visible-block boundaries, and the fallback order.

## Purpose

- Give detector changes a shared reference for what "thinking", "working", "done", "needs input", and "needs approval" look like across Claude and Codex versions.
- Keep prompt scraping rules grounded in raw examples so a detector regression can be tested with a copied terminal fixture.
- Record the agent-to-agent communication methods YOLOmux can use, including which ones are primary channels and which ones are fallback channels.

## Implementation Owners

- Visible Claude/Codex tmux pane control: `yolomux_lib/agent_tui.py`. This is the public API for capture, cursor facts, composer state, prompt/screen classification, clearing, paste-submit, and send verification.
- Pure visible screen state classification: `yolomux_lib/prompt_detector.py::agent_screen_state`.
- Pure approval prompt classification: `yolomux_lib/prompt_detector.py::approval_prompt_state`.
- Claude AskUserQuestion text: `yolomux_lib/prompt_detector.py::ask_user_question_prompt_text`.
- Generic visible user-choice question text: `yolomux_lib/prompt_detector.py::visible_choice_prompt_text`.
- Transcript activity state: `yolomux_lib/transcripts.py::transcript_activity_state_from_text`.
- Hybrid approval source order: `yolomux_lib/approvals.py::hybrid_approval_prompt_state`.
- App status fan-out from pane text, approval state, and transcript state: `yolomux_lib/app.py::TmuxWebtermApp.prompt_and_screen_status`, routed through `agent_tui.classify_agent_pane`.
- Claude/Codex structured stream normalization for YO!agent and text clients: `yolomux_lib/agent_comms/stream_events.py`. Current Claude CLI stream-json can emit Anthropic-style partials either directly or wrapped as `{"type":"stream_event","event":{...}}`; normalizers must unwrap those events, preserve outer `session_id` metadata, separate assistant-visible text from thinking/tool-call auxiliary rows, and dedupe snapshot tool-use echoes without dropping the later tool output.
- Regression fixtures: `tests/test_auto_approve_detector.py` and `tests/test_transcripts.py`.

When one tmux session contains multiple detected Claude/Codex panes, each tmux window keeps its own agent kind, pane target, cwd/path, prompt state, and working/idle evidence. Session-level YO remains a stored user toggle, but status collection fans out to the live agent pane targets; UI surfaces such as the tmux window bar, Tabber, popovers, and Recent Agents must render the per-window rows instead of collapsing them into one session agent. A legacy scalar session status may still choose the selected/preferred agent pane first and then a deterministic fallback, but that scalar is only a summary and must not overwrite per-window state/path/branch rows.

## State Model

| State | Meaning | Safe to type? | Primary detection source |
| --- | --- | --- | --- |
| `working` | The agent is currently generating, running a tool, reviewing files, or has a recent pending tool call. | No. Do not inject user text into a live turn. | Newest visible working line or recent transcript activity. |
| `idle` | The agent is done and the input prompt/composer is available. | Yes, if the target pane is the intended pane. | Bottom prompt/composer with no live working block after it. |
| `needs-input` | The agent is waiting for a non-permission user decision, such as a multiple-choice question. | Only through the question-answer workflow; normal prompt sends must not paste over it. | Current visible question block or Claude AskUserQuestion UI. |
| `approval` | Claude or Codex is waiting for a permission decision. | Yes, but use the approval action for the selected option; do not type arbitrary text unless rejecting with instructions. | Visible approval prompt block, optionally enriched by transcript/tool context. |
| `blocked/error` | The agent cannot continue without external correction, a crash recovery action, or a stale session reset. | Usually no; route through session-specific recovery. | Explicit error text, repeated stale state, or app-level health checks. |

## Detector Principles

- Prefer structured event channels over terminal scraping whenever they exist. Terminal text is a fallback because TUI wording, glyphs, and layout change across versions.
- Claude terminal UI changes very frequently. Keep matching shape-based and evidence-based, preserve raw/styled captures plus cursor facts with the installed CLI version, and update fixtures from real captures when drift appears. Never make one Claude release, randomized status verb, footer sentence, or prompt glyph the long-term contract.
- Store prompt corpus examples as `*.yaml` agent_tui captures with `raw_capture`, `styled_capture`, client metadata, cursor facts, operations, failures, and expected detector state. All current prompt-corpus capture and synthetic fixtures use `width: 78` and `height: 35`; keep future fixtures at `78x35` with `capture_prompt_fixture.py` unless the case is explicitly about width or height variants, and record the chosen width/height in the fixture metadata. Plain live `mock <case>` reconstructs hard-wrapped prose rows from the recorded fixture width, re-renders them to the current pane width, and stretches separator-only and box-border rows that exactly hit the fixture width so the mock looks like a live TUI. It must not merge menu options, prompts, footers, command/tool rows, or fresh assistant rows. Frozen `mockcase <case>` and `--dump-fixtures` preserve the recorded `78x35` evidence. Write multiline capture fields as YAML block scalars so raw terminal text is readable in review. Temporary verifier output may start under `tests/fixtures/prompt_corpus/staging/`, but useful cases should be promoted and staging can be discarded; promoted captures keep `source_staging_file` when staging provenance exists. Every capture filename plus YAML metadata must include the case name, normalized client token, and capture date, with the date or timestamp as the final filename component. Use `case_name__client-token_date.yaml`, where client tokens keep the CLI name and version visually readable, such as `codex-cli-0.141.0`, `claude-code-2.1.183`, or `codex-cli-synthetic`; synthetic cases live under `tests/fixtures/prompt_corpus/synthetic/`, promoted real TUI captures live under `tests/fixtures/prompt_corpus/captures/`, and the root inventory may reference either location for detector coverage.
- Prompt corpus parity is semantic. Do not assume every Claude capture needs a same-name Codex capture or the reverse. `working_command_counter` is Codex-only by name because the live repro was a command-send scenario, but its Claude counterpart is `working_visible_counter`; both are the `visible-working-counter` group and both prove `RUN`. Product-specific surfaces stay one-sided when the other client has a different product feature: Claude file/tool/Bash permissions are not Codex network/sandbox/MCP approvals, and Claude interrupted `What should Claude do instead?` has no current Codex question counterpart. Synthetic-only rows that still need live evidence are listed under `live_capture_targets` in `tests/fixtures/prompt_corpus/inventory.yaml`; replace those with real captures when the exact upstream prompt is available instead of inventing a cross-product pair.
- For YO!agent send-result watchers, transcript result text wins first, transcript-derived edited-file deltas win next, and visible pane scraping is only a final fallback when those stronger signals are missing or stale.
- Use the newest bounded visible block. Do not scan the whole scrollback and treat an old prompt as current.
- A live working line wins over older questions above it. A current approval prompt wins over an older working header above it.
- Visible status counters are screen-local activity evidence. A shape match such as a spinner/status marker plus elapsed time, token count, effort/progress text, or interrupt hint can mark the pane `working`; advancement of elapsed time, tokens, or tool-use counts across captures is stronger evidence than one copied row, and repeated unchanged counters may become stale when no transcript/file/tool activity still proves work is active.
- Footer/chrome/task rows are not later activity or user questions by themselves. Examples include `esc to interrupt`, `bypass permissions`, `⏸ plan mode`, context usage, model/effort labels, Ctrl-T task rows, Claude `Tip:` rows, and composer boxes.
- Composer ghost detection must use cursor facts when available. Claude can leave NBSP/suggestion-looking spacing after the user types into the composer; if the cursor has advanced past the candidate text, treat it as a real draft that can be cleared, not a ghost suggestion.
- A real shell prompt below a working row means the working row is stale.
- A bare user prompt at the bottom must stop stale-question scanning. Example: an old `❯ Where are the DOIT files?` above a current `❯ ` prompt is not `needs-input`.
- Tests should use raw copied panes with an expected state. When a new Claude/Codex version changes a glyph or footer, add the fixture before relaxing the detector.

## Claude Working Patterns

Claude working rows often use a leading glyph, a randomized verb, elapsed time, token direction/count, and sometimes effort or interrupt hints.

```text
✱ Imagining… (4s · ↓ 98 tokens)
✦ Comboublahblah… (7s · ↓ 123 tokens)
✳ Doodooshit… (1m 2s · ↓ 1.2k tokens)
☉ Refactoring... (2.3s · ↑ 13 tokens · high effort)
✶ Thinking… (1s · ↑ 26.9k tokens · esc to interrupt)
● Lollygagging… (2m 1s · ↓ 8.0k tokens · thinking with xhigh effort)
● Honking… (1m 12s · ↓ 5.8k tokens)
✽ Tomfoolering… (7m 12s · ↓ 30.1k tokens · almost done thinking with xhigh effort)
. Tomfoolering… (10m 35s · ↓ 55.4K tokens)
· Tomfoolering… (12m 49s · ↑ 64.3k tokens)
```

Claude may also show multi-agent progress instead of a single "Thinking" row.

```text
⠿ Running 2 agents…
  ├ Verify detector fixtures · 14 tool uses · 31.2k tokens
  └ Check current Claude pane state · 23 tool uses · 77.5k tokens

(ctrl+b to run in background)
```

Claude background-agent rows can also carry live counters. A row such as `○general-purpose Audit ui_tree followups DOIT 2m 15s · ↓ 69.1K tokens` is activity evidence when the elapsed, token, or tool-use counter advances.

Claude chrome below a live working row should not make the detector think the turn is done.

```text
✶ Thinking… (1s · ↑ 26.9k tokens · esc to interrupt)
100% context used
▶▶ bypass permissions on · 1 shell · esc to interrupt
```

```text
● Lollygagging… (2m 1s · ↓ 8.0k tokens · thinking with xhigh effort)

╭────────────────────────────────────────────╮
│ >                                          │
╰────────────────────────────────────────────╯
⏺ xhigh /effort
▶▶ bypass permissions on · 1 shell · esc to interrupt
```

Claude Ctrl-T task rows below a working footer are still trailing UI, not proof of idle state. Known task glyphs include `□`, `✓`, `✔`, `✗`, `✘`, `◯`, `☐`, `☑`, `☒`, `▢`, `▣`, `◻`, `◼`, `◐`, and `◓`.

## Claude Done And Typeable Signals

Claude is done when there is no live working row after the latest assistant output and the composer is visible. A completed message plus a boxed composer is idle.

```text
● Updated 3 files and finished the task.

╭────────────────────────────────────────────╮
│ >                                          │
╰────────────────────────────────────────────╯
▶▶ bypass permissions on · 1 shell
```

A boxed input with effort chrome but no live working line is idle.

```text
╭────────────────────────────────────────────╮
│ >                                          │
╰────────────────────────────────────────────╯
⏺ xhigh /effort
```

The old bare Claude prompt can also indicate typeable idle state when it is the bottom current prompt.

```text
❯
```

## Claude Needs-Input Patterns

Claude can ask the user a normal multiple-choice question in the pane. This is `needs-input`, not an approval prompt.

```text
Which backend should I use?
❯ 1. vLLM
  2. SGLang
```

Claude AskUserQuestion UI may not use the `❯` selector; detect it by the question, two or more numbered choices, and the footer with selection/navigation hints.

```text
How should the YO!info | YO!agent sub-tab toggle look inside the merged panel?
  1. Segmented control under pane tabs
  2. Pills in the content header
┌──────────────────────────────┐
│ Preview: segmented control…   │
└──────────────────────────────┘
Notes: press n to add notes
Chat about this
Enter to select · ↑/↓ to navigate · n to add notes · Tab to switch questions · Esc to cancel
```

This stale example is idle, not `needs-input`, because the current bottom prompt is bare and the question is old scrollback.

```text
❯ Where are the DOIT files?

● They're gone from ~/yolomux.dev8002 — and that's by the project's design, not a loss.

✻ Baked for 1m 33s · 1 shell still running

❯
```

## Claude Approval Patterns

Claude bash permission prompts usually contain a Bash command header, a command block, a permission-rule sentence, a question, Yes/No options, and a footer.

```text
─────────────────────────────────────────────
 Bash command

   rm -rf /tmp/foo
   Delete temp directory

 Permission rule Bash requires confirmation for this command.

 Do you want to proceed?
 ❯ 1. Yes
   2. No
 Esc to cancel · Tab to amend · ctrl+e to explain
```

Claude file-edit prompts ask about editing a specific file and usually offer multiple Yes variants plus No. They should be classified as `approval` with prompt type `file`, not as a generic user question.

```text
Do you want to make this edit to SKILL.md?
❯ 1. Yes
  2. Yes, and allow Claude to edit its own settings
  3. No
```

When extracting a command from a Claude prompt, bound the search to the current prompt block. Do not walk past a separator into an earlier `● Bash(...)` block or an earlier approved step.

## Codex Working Patterns

Codex working rows commonly use `◦`, `•`, or `○`, a short activity label, elapsed time, and `esc to interrupt`.

```text
◦ Working (1m 21s • esc to interrupt)
• Working (6m 38s • esc to interrupt)
◦ Reviewing files (24s • esc to interrupt)
○ Working (4m 09s • esc to interrupt)
```

Codex can show a working row above an input-looking composer and model line. The working row is still current unless a real prompt or real later activity proves it stale. Codex goal-status suffixes such as `Pursuing goal (<duration>)` and `Goal achieved (<duration>)` are status chrome; when paired with a live working row, their duration is the cumulative goal elapsed time for display.

```text
○ Working (33m 05s · esc to interrupt)
╭────────────────────────────────────────────╮
│ Use /skills to list available skills       │
│ >                                          │
╰────────────────────────────────────────────╯
gpt-5.5 xhigh   ~/yolomux.dev8002
```

The exact model/effort text is not the contract. Older Codex captures may show `gpt-5.5 xhigh`; the local `tools/codex.py` wrapper defaults to `gpt-5.4-mini medium`.

Codex working examples embedded in explanatory text are not live state when a bottom composer follows them as part of an old response.

```text
  Then sleep 10 approval should show:

  • Working (10s • esc to interrupt)

  with Working animated in the real TTY.

› Explain this codebase

  gpt-5.4-mini medium · ~
```

## Codex Done And Typeable Signals

Codex is typeable when the bottom composer prompt is visible and there is no current working row after the latest real activity.

```text
› Explain this codebase

  gpt-5.4-mini medium · ~
```

A real shell prompt below a Codex working line makes the working line stale.

```text
○ Working (4m 09s • esc to interrupt)
user@host$ echo done
```

## Codex Approval Patterns

Codex command approvals ask whether to run a command, may include a reason, show the command with `$`, and offer three options.

```text
◦ Running gh api repos/ai-project/project/pulls/9579/comments

  Would you like to run the following command?

  Reason: Do you want to allow GitHub network access so I can fetch PR #9579 status?

  $ gh api repos/ai-project/project/pulls/9579/comments

› 1. Yes, proceed (y)
  2. Yes, and don't ask again for commands that start with `gh api` (p)
  3. No, and tell Codex what to do differently (esc)
```

Codex may select option 2 by default for remembered prefixes. The detector must report the selected option instead of assuming option 1 is selected.

```text
  Would you like to run the following command?

  $ curl -sk -u yolomux:yolomux https://localhost:7777/

  1. Yes, proceed (y)
› 2. Yes, and don't ask again for commands that start with `curl -sk -u` (p)
  3. No, and tell Codex what to do differently (esc)
```

## Transcript Signals

Claude transcript activity is `working` while an assistant message is streaming, while `tool_use` blocks are pending without matching `tool_result`, or while recent payloads indicate deltas or task start. Terminal stop reasons such as `end_turn`, `stop_sequence`, `max_tokens`, and `stop` mark the assistant turn completed when there are no pending tools.

Codex transcript activity is `working` while recent payloads such as `agent_message_delta`, `message.delta`, `item.delta`, `task_started`, `function_call`, or `custom_tool_call` are active without completion or matching output. `task_complete`, `function_call_output`, and `custom_tool_call_output` clear the pending state.

Transcript state must be recency-gated. A stale transcript with an old pending-looking record must not override a current visible idle prompt.

## User Prompt Examples

Use these examples to test the difference between prompts the agent presents to the user and text that merely appears in prior conversation.

| Example | State | Why |
| --- | --- | --- |
| `Which backend should I use?` followed by `❯ 1. vLLM` and `2. SGLang` | `needs-input` | It is a current visible choice block. |
| AskUserQuestion footer `Enter to select · ↑/↓ to navigate · n to add notes · Tab to switch questions · Esc to cancel` | `needs-input` | It is a modal decision UI, not a permission prompt. |
| `Do you want to make this edit to SKILL.md?` with Yes/No options | `approval` | It is a tool/file permission decision. |
| `Would you like to run the following command?` with `$ <command>` and Codex options | `approval` | It is a command permission decision. |
| Bottom composer ghost suggestion text such as `❯ commit the DYN_PARSER_DEBUG change` or dim Codex text like `› Summarize recent commits` | `idle` | It is placeholder UI exposed by tmux capture, not a draft the user typed; Claude can preserve NBSP spacing and Codex can preserve ANSI dim styling. |
| Old `❯ Where are the DOIT files?` above a current bare `❯ ` prompt | `idle` | The bottom prompt proves the old question is scrollback. |
| Text explaining `• Working (10s • esc to interrupt)` followed by the bottom Codex composer | `idle` | The working row is an example in conversation, not live chrome. |

## Agent-To-Agent Communication Methods

| Method | Pros | Cons | Recommendation |
| --- | --- | --- | --- |
| Structured Claude permission hooks, such as a `PreToolUse` hook | Gives machine-readable approval events before the TUI prompt path, avoids guessing from scrollback, can preserve command metadata. | Requires user config and hook install; failures must fall back to visible prompt detection. | Recommended primary path for Claude permission decisions. |
| Codex app-server / JSON-RPC supervisor approval handling | Gives structured command approval requests such as `item/commandExecution/requestApproval`, can answer without scraping terminal text. | More integration work; exact protocol and launch mode can change across Codex versions. | Recommended primary path for Codex command approvals when available. |
| Transcript tailing | Good read-only source for recent activity, pending tool calls, history, and state recovery; does not disturb the pane. | Can lag, can be stale, and may omit the current TUI selector; needs recency gates and visible confirmation for approvals. | Use as passive state enrichment and fallback activity detection. |
| YOLOmux HTTP/SSE/WebSocket APIs | Good for browser/server coordination, rosters, pane state, and internal UI events. | Not an external agent protocol unless authenticated, scoped, and versioned; can couple unrelated features if used as a general bus. | Use for YOLOmux internal state, not as the first agent-control channel. |
| File or JSONL mailbox in the workspace | Simple, durable, inspectable by humans, and works across tools with no TUI dependency. | Not realtime; needs locking, schema, ownership, and cleanup; easy to create ambiguous half-written records. | Use for durable task handoff, DOIT-style queues, and cross-agent notes. Prefer JSONL for machine state and Markdown for human work queues. |
| Local MCP/tool server | Structured bidirectional actions, typed tools, and clearer capability boundaries. | Setup overhead, versioning, auth, and lifecycle management are real costs. | Recommended for richer cross-agent workflows once the tool boundary is known. |
| Tmux text injection and visible-screen scraping | Universal, easy to debug, and works when no structured channel exists. | Brittle against TUI wording/layout changes, can hit the wrong pane, can type during a live turn if state is wrong. | Fallback only. Keep fixtures current and gate injection on `idle`, `needs-input`, or `approval`. |
| Shared tmux windows with labeled panes | Human-readable and useful for manual handoff or emergency recovery. | Weak structured state, naming collisions, no reliable acknowledgement by itself. | Use as a human-facing coordination surface, not as the only state source. |
| OSC 52 clipboard bridge | Useful for copy/paste flows when terminal selection is unreliable. | Clipboard is global-ish user state, not a command/control channel; can overwrite user clipboard. | Use only for explicit copy bridge features, not agent orchestration. |

## Recommended Channel Order

1. Use structured permission/event APIs for approvals and high-risk actions: Claude hooks for Claude, Codex JSON-RPC/app-server paths for Codex.
2. Use transcripts and event streams for passive activity detection, pending-tool context, and history.
3. Use a file/JSONL mailbox or Markdown work queue for durable agent-to-agent task handoff.
4. Use YOLOmux internal HTTP/SSE/WebSocket paths for YOLOmux UI/server state.
5. Use tmux scraping/injection as the compatibility fallback and emergency path.

## Test Guidance

- Add a copied raw terminal fixture for every detector bug before changing the detector. For Claude, include the installed client version and enough raw/styled/cursor evidence to prove the match is based on UI shape, counters, and position rather than the current novelty text.
- Assert both the low-level helper and `agent_screen_state`, because the browser badge path depends on the combined state.
- Include stale scrollback cases: old approval above later activity, old question above bare prompt, old working example in a response, and real shell prompt below working text.
- Include trailing chrome cases: composer boxes, effort/model labels, context lines, task lists, and interrupt footers.
- Include selected-option cases for Codex approvals. The visible selected row may be option 2, not option 1.
- Keep transcript tests separate from visible-screen tests. Transcript state is supporting evidence unless the structured approval path is explicitly the source of truth.
