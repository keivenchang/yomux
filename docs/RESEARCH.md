# Product Research and Peer Landscape

This document records product and architecture research that can inform YOLOmux. It is not a commitment to adopt a peer's design or replace the current tmux substrate. Update it when a comparison changes a product or technical decision.

YOLOmux is a lightweight, powerful AI-work workspace: it combines AI management, editing and viewing, collaboration, file and Git context, and observability. Existing tmux sessions are one useful integration surface, not the product's defining theme. See the [runtime architecture](../README.md#runtime-architecture).

## How this research maps to the roadmap

[`queues/README.md`](../queues/README.md) sets the product boundary: YOLOmux centers AI management, editing/viewing, collaboration, and a lightweight but capable workspace; borrowed ideas must strengthen that control loop; event/audit data comes before a timeline; and broad multi-machine, canvas, or pipeline products wait. The findings below support those guardrails. They are evidence for small, testable additions—especially durable agent jobs, handoffs, approvals, session visibility, and collaboration—not a mandate to build a distributed orchestration platform.

## Peer map and availability snapshot

Status is a dated, high-level project-source snapshot (2026-07-11), not legal advice. “No paid plan stated” means the referenced project page did not state one; it does not prove that paid support or hosting is unavailable elsewhere.

| Project | Primary job | Availability / license | Relationship and lesson |
| --- | --- | --- | --- |
| [tmux](https://github.com/tmux/tmux) | Mature Unix terminal multiplexer | ISC; open source | YOLOmux's terminal/session substrate. Preserve tmux compatibility and reliability; differentiate above it through browser, agent, file/Git, and collaboration layers. |
| [RMUX](https://rmux.io/) | Cross-platform, agent-programmable multiplexer with typed SDKs | PolyForm-Noncommercial-1.0.0 OR MIT; open source | Closest architectural reference for automation and browser sharing. Borrow contracts, not its substrate, unless a separate migration is approved. |
| [Zellij](https://github.com/zellij-org/zellij) | Workspace-oriented terminal multiplexer | MIT; open source | Its layouts, WebAssembly plugins, built-in web client, and collaboration show the value of discoverable workspace templates and an extensibility boundary. |
| [WezTerm](https://github.com/wezterm/wezterm) | Cross-platform GPU terminal and multiplexer | MIT; open source | A quality bar for terminal rendering and a reminder that terminal emulator, multiplexer, and browser workspace are distinct layers. |
| [tmate](https://github.com/tmate-io/tmate) | Instant terminal sharing built around tmux | ISC; open source | Reference for the entry-point simplicity a future, separately approved terminal-sharing product would need. |
| [ttyd](https://github.com/tsl0922/ttyd) | Run/share one command in a browser terminal | MIT; open source | A focused benchmark for “one command → authenticated browser terminal,” including file transfer and broad platform support. |
| [sshx](https://github.com/ekzhang/sshx) | Collaborative live browser terminal | MIT; open source | Its shared cursors, reconnection/latency feedback, and encrypted collaboration are strong UX/security references; its hosted mesh and lack of supported self-hosting are deliberate tradeoffs. |
| [Coder](https://github.com/coder/coder) | Self-hosted cloud workspaces and agent governance | AGPL-3.0 community source; paid Premium offering | Different scope, but a strong reference for templates, workspace lifecycle, audit/governance, and secure remote access at organizational scale. |
| [Warp](https://www.warp.dev/) | Commercial agentic terminal and coding-agent platform | Proprietary commercial product; free tier plus paid plans | A useful product-quality reference for multi-harness agent workflows, data controls, usage visibility, and polished onboarding—not a code or licensing source. |
| [OpenCode](https://github.com/anomalyco/opencode) | Terminal, desktop, and IDE coding-agent harness | MIT; open source | A relevant harness that YOLOmux can observe and supervise alongside Claude/Codex; it is not a multi-session workspace manager. |

Primary references: [Zellij project](https://github.com/zellij-org/zellij), [ttyd project](https://github.com/tsl0922/ttyd), [sshx project](https://github.com/ekzhang/sshx), [Coder project](https://github.com/coder/coder), [Coder plans](https://coder.com/pricing), and [Warp plans](https://www.warp.dev/pricing).

### Internal-site pricing endpoint (2026-09-10)

The internal site's public pricing page is the internal pricing page. Its frontend requests model-rate JSON from an internal rates endpoint; the response is expected to contain a `rates` array with `model`, `price_1m_input_tokens`, `price_1m_output_tokens`, `price_1m_cache_read_input_tokens`, `provider`, and `rate_source`. Direct unauthenticated requests currently return the HTML shell rather than JSON, so the browser's tenant/auth context or headers are required. YOLOmux currently uses the packaged `yolomux_lib/data/model_pricing.json` seed and OpenAI-source fallback/inference instead of fetching the internal endpoint. In particular, `openai/openai/gpt-5.6-luna` is unpriced because duplicate provider prefixes are not fully canonicalized before catalog lookup; the canonical Luna entry is `openai` + `gpt-5.6-luna`.
The browser frontend talks to a protected internal backend; direct calls without the authenticated tenant cookie/token do not return the JSON rates. A user-provided authenticated snapshot was captured from `https://inference-backend.internal/api/v1/cost/rates` on 2026-09-10: 200 records total, 176 with non-null prices, including `openai/openai/gpt-5.6-luna` at `$0.1468` input, `$0.8808` output, `$0.01468` cache-read input, `$0.3303` blended 3:1, maximum input 1,050,000, maximum output 128,000, provider `openai`, source `litellm`. The exact 53,540-byte payload is stored at `yolomux_lib/data/internal_rates_2026-09-10.json` with SHA-256 `879b1daf8417fcd9ad99926b90af327320f16c959c998b01f6bb9daee9e94250`; `tools/import_internal_rates.py` validates the 200/176 metadata and generates the normalized 98-model overlay `yolomux_lib/data/internal_model_pricing.json`. The packaged catalog now overlays the internal Luna rate while retaining the older seed prices for existing historical models. Do not substitute public-provider rates for internal rates: the internal endpoint's `provider`, `rate_source`, tenant, and model identity must be preserved as pricing evidence.

### Cross-project findings

1. **Keep the layer boundaries clear.** tmux, Zellij, RMUX, and WezTerm own terminal/session substrate concerns. YOLOmux should continue to make the existing tmux workspace legible and controllable in a browser rather than becoming a second terminal emulator.
2. **Make workspace templates first-class.** Zellij's layout model and Coder's provisioned workspaces both make repeatable starting state explicit. YOLOmux can add named, URL-backed workspace templates for common work such as investigation, review, incident response, and multi-agent coordination, without creating or replacing tmux sessions implicitly.
3. **Treat collaboration as a product surface.** tmate and sshx show that any future remote-session collaboration product would need deliberate presence, reconnection, latency, authorization, expiry, and revocation contracts. Current YOLOmux collaboration remains authenticated chat and agent handoffs.
4. **Separate local UX from organizational control.** Coder's template, identity, audit, workspace-cleanup, and governance model is relevant only when YOLOmux is used across an organization. It should inform optional administration boundaries, not turn the local-first app into a cloud workspace provider.
5. **Agent orchestration needs observable, harness-neutral state.** RMUX and Warp both emphasize automation around terminal/agent workflows. YOLOmux's advantage is its real Claude/Codex context and tmux history; improve it with explicit jobs, safe handoffs, structured result markers, and durable traces rather than generic text scraping.

## Focused project profiles

### Zellij — workspace UX, web access, and extensions

Zellij is a MIT-licensed terminal workspace with layouts, WebAssembly plugins, and true multi-user collaboration. Its official project describes floating and stacked panes, layouts, plugins, and a built-in web client. The web server is off by default and uses authentication/session management; its sharing model can expose existing sessions or start new ones. Sources: [project README](https://github.com/zellij-org/zellij), [web client guide](https://zellij.dev/documentation/web-client.html), and [FAQ](https://zellij.dev/faq/).

**Take for YOLOmux:** define named, inspectable layouts/templates and preserve their exact URL state; add a deliberately scoped extension boundary only when there is a real owner and sandbox story. Do not copy Zellij's terminal substrate, pane model, or plugin runtime merely to add plugins.

### sshx, tmate, and ttyd — collaboration at three useful levels

- **tmate** is the reference for immediate terminal sharing from a tmux-shaped workflow. Its lesson is a low-friction entry point, not a sufficient authorization/audit model for YOLOmux.
- **ttyd** is a small MIT-licensed baseline: one command exposed in a browser terminal, with TLS, authentication, file transfer, and broad platform support. It usefully tests whether a YOLOmux action has grown more complex than the problem it solves. [ttyd](https://github.com/tsl0922/ttyd)
- **sshx** is MIT-licensed and adds an infinite canvas, shared cursors, automatic reconnection, latency feedback, predictive echo, and documented end-to-end encryption. Its upstream README says supported self-hosted deployment is not currently offered, which is an important operational tradeoff. [sshx](https://github.com/ekzhang/sshx)

**Take for YOLOmux:** keep current collaboration within authenticated chat and agent handoffs. Any future remote-session product starts as a new product decision with explicit roles, expiry, revocation, reconnection behavior, threat modeling, and security review.

### Coder — governed remote workspaces, not a terminal pane manager

Coder's AGPL-3.0 community source provisions self-hosted development environments through Terraform and WireGuard, can stop idle workspaces, and positions agent use around central identity, cost, audit, and governance. Its published plans describe a free Community tier and a paid Premium tier with organizational controls, audit logging, high availability, and global workspace proxies. Sources: [source repository](https://github.com/coder/coder) and [plans](https://coder.com/pricing).

**Take for YOLOmux:** if shared/organizational deployment becomes a product need, borrow the boundary—not the platform: identity-aware access, audit events, environment lifecycle, and explicit resource ownership. YOLOmux should not start provisioning cloud infrastructure or force a Terraform workspace model for local tmux users.

### Warp and OpenCode — harness ergonomics versus harness supervision

Warp is proprietary with a free tier and paid individual, business, and enterprise plans. Its published plans emphasize using several agent harnesses, data controls, spend/usage visibility, shared objects, and enterprise governance. [Warp plans](https://www.warp.dev/pricing)

OpenCode is an MIT-licensed open-source coding agent with terminal, desktop, and IDE surfaces. [OpenCode documentation](https://opencode.ai/docs) and [source](https://github.com/anomalyco/opencode)

**Take for YOLOmux:** users will run more than Claude and Codex. Keep the agent-window model extensible: separate a provider-neutral lifecycle/status contract from provider-specific prompt detection and transcript parsing. A new harness should add its precise capability adapter rather than a second generic screen scraper. YOLOmux's job is to supervise many harnesses in a workspace, not to become another coding harness.

## AI coordination and agent management — 2026-07-11

This section distinguishes _agent coordination_ from simply running several agents. Coordination requires a durable job identity, a declared state model, defined handoff/approval points, observable transitions, cancellation and timeout behavior, and evidence for completion. A free-form chat among agents is not sufficient for work that can edit code or wait across a server restart.

| System | Model | Availability / license | Useful lesson for YOLOmux |
| --- | --- | --- | --- |
| [Microsoft Conductor](https://github.com/microsoft/conductor) | Version-controlled YAML graph for multi-agent and script workflows | MIT; open source | Make routing, joins, limits, inputs, and human gates explicit and reviewable; separate deterministic workflow control from LLM work. |
| [Conductor OSS](https://github.com/conductor-oss/conductor) | Distributed durable workflow engine that can orchestrate agents | PolyForm-Noncommercial-1.0.0; open source | Persist every step, retry safely, and resume after failure; do not adopt its heavyweight distributed platform for local YOLOmux jobs. |
| [LangGraph](https://langchain-ai.github.io/langgraph/) | Stateful graph-based agent workflows and supervision patterns | MIT; open source | Model coordination as explicit state transitions and bounded handoffs, not an unbounded group conversation. |
| [CrewAI](https://github.com/crewaiinc/crewai) | Role/task “crews” plus event-driven “flows” | MIT source; commercial AMP control plane | Separate flexible agent collaboration from precise stateful workflow execution; observability/governance is a control-plane concern. |
| [AutoGen / Microsoft Agent Framework](https://github.com/microsoft/autogen) | Message-passing multi-agent framework; successor framework supports A2A/MCP | AutoGen code MIT and docs CC-BY-4.0; AutoGen is in maintenance mode | Avoid binding new work to an ageing abstraction; retain provider-neutral message/event boundaries so future protocols remain optional adapters. |

### The closest design reference: Microsoft Conductor

Microsoft Conductor coordinates existing agent runtimes rather than requiring a new terminal substrate. It defines agents, prompts, typed outputs, scripts, conditional routes, parallel groups, sub-workflows, explicit success/failure termination, iteration/timeout limits, and human decision pauses in YAML. Routing uses templates and expressions rather than an LLM deciding what runs next, so the workflow can be reviewed in Git and run consistently in local or CI contexts. Its dashboard shows the live DAG, outputs, activity, model/tokens and cost data, plus human gates. Sources: [Conductor README](https://github.com/microsoft/conductor) and [provider details](https://github.com/microsoft/conductor#providers).

**Take for YOLOmux:** retain the server-owned job model, but evolve complex multi-agent work into a small declarative state machine. A YOLOmux workflow should refer to session/window capabilities and artifact contracts, not raw shell commands or arbitrary panes. The controller must make these facts first-class and visible:

1. `workflow_id`, `run_id`, source revision, inputs, target identities, and the current state/transition reason.
2. Explicit `send`, `wait`, `collect`, `verify`, `fanout`, `join`, `human_gate`, `cancel`, and terminal `success`/`failed` states.
3. Schema-checked artifacts/results; an agent's prose never silently decides a route or triggers a privileged action.
4. Bounded retries, timeouts, idempotency keys, and resume behavior after a browser/server disconnect.
5. A graph/timeline UI that links to the exact session, transcript evidence, changed files, test command/result, and approval responsible for each step.

### Durable execution versus interactive supervision

Conductor OSS is an PolyForm-Noncommercial-1.0.0 workflow engine designed to persist every step and resume after a worker crash. That is the right reliability principle for YOLOmux jobs such as “wait for three agents, verify tests, then ask a fourth to review,” but it is not a reason to install a Java/Redis/database workflow platform inside a local tmux UI. Sources: [Conductor OSS README](https://github.com/conductor-oss/conductor) and [agent workflow overview](https://github.com/conductor-oss/conductor#agentic-workflows).

YOLOmux should use a lighter durable record in its existing state directory: append-only transition/audit records plus a compact current-job snapshot. Each transition must be replay-safe; each outgoing agent send needs an idempotency/result marker; and restart recovery must re-observe the target instead of assuming an old “working” state is still true.

### Patterns to adopt, with limits

- **Supervisor routes, workers execute.** A controller can choose among a bounded set of declared next states; workers cannot rewire a workflow or send to undisclosed sessions.
- **Fan-out has a join contract.** Declare all/any/quorum completion, per-target timeout, evidence requirements, and unfinished-work behavior. Do not infer completion from a quiet terminal alone.
- **Human gates are durable state.** Persist who must decide, proposed action, evidence, expiry, and accepted/rejected result. A workflow cannot advance merely because a browser UI disappeared.
- **Artifacts beat transcript relays.** For large analysis, patches, or test reports, write a named artifact with a size/schema check; hand off a path and summary rather than a complete terminal capture.
- **Observability is not optional.** CrewAI's split between flexible crews and event-driven flows, plus its control-plane traces/governance, reinforces that status, logs, cost, and outputs need structured records. [CrewAI](https://github.com/crewaiinc/crewai)
- **Interoperability is an adapter layer.** AutoGen is in maintenance mode and its successor points toward A2A/MCP. Keep YOLOmux's Claude/Codex/OpenCode adapters and internal event schema independent from any one protocol.

### Non-goals

- Do not add an unconstrained “agent swarm” chat that recursively delegates work without an owner, budget, target allowlist, or human stop path.
- Do not make model output the source of truth for job state, access policy, retries, or success.
- Do not invoke a generic orchestration framework merely to perform one safe send-and-wait action; the existing direct job path should remain simple.
- Do not expose transcript text, credentials, or write capability to a worker that needs only a bounded artifact or read-only status.

### Recommended coordination roadmap

| Priority | Work | Completion evidence |
| --- | --- | --- |
| P0 | Document a durable YO!agent job/run schema and transition audit format. | Restart/recovery test proves no duplicate send and shows exact transition history. |
| P0 | Move existing wait/send/roster/loop/result jobs onto one workflow state machine. | Existing behavior is unchanged; a graph/timeline view is derived from one owner. |
| P1 | Add declared fanout/join and human-gate states with artifact contracts. | Tests cover all/any/quorum, timeout, cancel, rejection, reconnect, and stale target state. |
| P1 | Add an operator view for run status, evidence, and cancellation. | A user can answer “what is running, why, and what happened?” without terminal scraping. |
| P2 | Evaluate import/export compatibility with a YAML workflow format. | A documented workflow round-trips without granting raw shell or arbitrary-pane authority. |

## RMUX research — 2026-07-11

RMUX presents itself as a Rust multiplexer that can retain tmux-compatible workflows while exposing daemon-owned shells through typed Rust, Python, and TypeScript SDKs. Its primary distinction is treating the terminal as an automation target: stable pane identity, structured snapshots, visible-text locators, output/render streams, waits, input, and trace capture are public API concepts rather than shell-script conventions. Sources: [product overview](https://rmux.io/), [architecture and automation model](https://rmux.io/docs/get-started/), and [API reference](https://rmux.io/docs/api/).

### RMUX is not a tmux API

tmux already has a powerful control surface: CLI commands such as `capture-pane` and `send-keys`, a socket-backed server, formats/hooks, and control mode (`tmux -C`) for text commands and events. YOLOmux uses that real tmux substrate today. The practical gap is not that tmux is uncontrollable; it is that callers must assemble target strings and commands, parse terminal or control-protocol output, and own retries/version behavior themselves.

RMUX is a replacement multiplexer with its own daemon, sessions, windows, panes, PTYs, and public typed SDKs; it does not wrap or control an existing tmux server. Its tmux-compatible CLI and filtered `tmux.conf` import are a migration path, not an adapter. The useful lesson for YOLOmux is to place a typed, tmux-backed automation adapter over the control paths it already has, rather than attempting a substrate swap. Sources: [RMUX tmux compatibility and config migration](https://rmux.io/docs/get-started/#configuration) and [RMUX architecture](https://rmux.io/docs/get-started/#architecture).

RMUX Web Share keeps the PTY local, supports separate operator and spectator roles, expiry/revocation and client limits, optional private/public tunnel presets, and an end-to-end encrypted browser transport. Its published security model says that URL-fragment tokens, pairing PINs, transcript-bound hybrid key exchange, per-frame authenticated encryption, strict sequence numbers, and origin/abuse controls are all part of the share contract. Sources: [Web Share guide](https://rmux.io/docs/web-share/) and [security/cryptography details](https://rmux.io/docs/web-share/#security-model).

### What YOLOmux can use now

1. **A first-class automation contract.** Define a small, versioned internal pane-automation API for YO!agent and tests: stable pane/session IDs, snapshot revision, `wait_for_visible_text`, bounded output/render stream, input result, and typed timeout/unsupported errors. This is more reliable than every caller combining `capture-pane`, prompt detection, polling, and ad-hoc string matching. It should wrap tmux first, not expose RMUX types.

2. **Terminal-native test primitives.** Add reusable visible-terminal assertions and quiet-state waits to the existing test helpers. These would complement—not replace—browser DOM tests: use terminal assertions for agent workflows and browser assertions for YOLOmux layout and controls. Record a bounded trace when an agent-routing test fails so failures can be reproduced without retaining unrestricted terminal history.

3. **Explicit ownership semantics.** RMUX's owned-session cleanup and daemon leases are a useful model for short-lived agent workspaces. YOLOmux already uses port and instance leases; extend that discipline only where it closes a real lifecycle gap, such as an explicitly app-created temporary session with a clear preserve-versus-cleanup policy.

4. **Capability negotiation.** RMUX advertises optional daemon capabilities and reports unsupported operations as typed diagnostics. YOLOmux can apply the same shape to optional tmux features, agent clients, and platform dependencies so the UI explains a missing capability rather than silently degrading or guessing.

### What not to adopt blindly

- **Do not replace tmux with RMUX as a feature task.** YOLOmux depends on real tmux sessions, existing user configuration, agent discovery, and production workflows. A replacement would be a separate migration program with compatibility, import, operational, and rollback plans.
- **Do not equate a future browser collaboration proposal with an E2EE claim.** RMUX's browser crypto is an end-to-end design with a static client trust boundary. Any comparable YOLOmux claim would require an equivalent threat model, independent review, key lifecycle, and browser-delivery guarantees.
- **Do not reduce agent semantics to text matching.** Visible-text locators are valuable for waits and tests, but YOLOmux still needs its Claude/Codex transcript, prompt-state, and per-window context model for safe routing.

### Recommended sequence

| Priority | Proposal | Evidence of success |
| --- | --- | --- |
| P1 | Design and implement a tmux-backed internal automation adapter with stable identity, snapshots, and bounded waits. | One YO!agent routing flow and its regression tests no longer duplicate terminal polling/parsing. |
| P2 | Add terminal-native assertion/trace helpers to test infrastructure. | A failing agent TUI regression reports a compact terminal trace and reproduces without sleep-based tests. |
| P2 | Add capability reporting for optional runtime features. | Unsupported features become specific, actionable UI/API diagnostics. |
| P3 | Evaluate an optional RMUX adapter only if Windows support or SDK-level lifecycle control becomes a product requirement. | Written compatibility matrix, user migration plan, and a reversible prototype—not a silent substrate swap. |

## Research hygiene

- Prefer primary documentation and source repositories.
- Date externally sourced claims because product capabilities change.
- Separate observed capability, inference, and proposed YOLOmux work.
- Do not turn a peer comparison into a roadmap commitment without a user need, security review, and an owner.

## Transparent model pricing and estimated cost — 2026-07-11

Implemented 2026-07-19. This section records the durable pricing, attribution, and uncertainty constraints behind the current displayed-range Cost summary; delivery sequencing is archived in `docs/DONE/` rather than remaining as a proposal.

### What current telemetry can and cannot prove

YOLOmux currently retains model-attributed generated-token rates for the Model tokens/min chart. That is useful for comparing model activity, but it is not a bill: providers commonly price uncached input, cache reads, cache writes, and output differently; some apply long-context, batch, priority, regional, or tool-use modifiers. A chart must never multiply a combined token total by one headline rate and present the result as an actual charge.

The authoritative references are provider-owned pricing tables, not a third-party aggregation. OpenAI publishes its current model catalog and per-model input/output/cached-input rates at [OpenAI API Models](https://developers.openai.com/api/docs/models) plus image input/output behavior and pricing units in [OpenAI Image generation](https://developers.openai.com/api/docs/guides/image-generation); Anthropic publishes its complete base-input, cache-write, cache-read, and output table at [Claude API Pricing](https://platform.claude.com/docs/en/about-claude/pricing); and Google publishes paid/free, standard/batch/flex/priority Gemini rates at [Gemini API Pricing](https://ai.google.dev/gemini-api/docs/pricing). These pages should always be linked beside a YOLOmux estimate. They change independently and do not provide one shared, stable cross-provider pricing API.

### Pricing catalog: dynamic updates without silent price changes

Use a server-owned, versioned pricing catalog with a reviewed packaged JSON seed and a local SQLite database under `YOLOMUX_CACHE_DIR/model-pricing/`. Each immutable catalog revision records provider, exact model IDs and aliases, effective date, source URL, source title, retrieval timestamp, source-content digest, parser/catalog revision, and distinct rates per million tokens for `input`, `cached_input_read`, `cache_write_5m`, `cache_write_1h`, and `output`; it also records conditions such as a long-context threshold, batch/flex/priority mode, regional multiplier, or an explicit `unsupported` marker. Model aliases must resolve deterministically to a row or remain unpriced—never guess from a partial model name.

The packaged catalog bootstraps a fresh/offline installation and reconciles into the local database without a startup network request. An admin-only `Refresh` action uses bounded conditional requests (`ETag` / `Last-Modified`) to allowlisted official URLs plus structured cross-references. A maintained provider adapter may activate an exact official row only when it recognizes the model/profile/component table shape and corroboration policy; new models, missing effective dates, disagreement, parser drift, and large changes remain `review-needed` while the prior catalog stays active. The UI exposes old/new rates, source URL, retrieved-at time, digest, parser version, and direct source links rather than silently trusting arbitrary scraped markup.

The Pricing settings/detail UI should show the active catalog revision, last verified and expiry times, model alias resolution, every applied rate/condition, source links, and refresh/review/override controls. A local override is the escape hatch for private endpoints, negotiated rates, Azure/Bedrock/Vertex contracts, and a user who wants a fully offline catalog. Catalog history is append-only and effective-dated so past usage is always repriced with the revision effective at its original timestamp.

### Displayed-range cost summary

Add an opt-in non-chart `Cost summary` immediately after Model tokens/min. Its heading is `Cost summary (est. $…, Σ displayed)` and it consumes the exact post-range/post-zoom bucket set already used by `Σ displayed`; it has no independent time selector, USD-per-minute plot, axis, or bars. Changing the YO!stats range, range slider, zoom, reset, or loaded-history boundary recomputes the heading and all breakdown rows from that same displayed interval. The estimate includes all models and billable classes in the interval regardless of Model tokens/min legend visibility.

The required telemetry extension is a stream of provider-neutral usage atoms keyed by provider/model, execution source, direction, modality, cache role, unit, effort, pricing context, and completeness. Provider adapters normalize their native records without double-counting cached subsets or reasoning tokens already included in output. Codex cumulative usage yields uncached-input/cached-input/output deltas and turn-context effort; Claude message/iteration usage yields per-request input/output and cache categories when present, while effort remains unknown unless explicitly captured. The existing `tokens` field remains the generated/output activity metric; it must not be retroactively relabeled as billable total tokens.

Usage quantity and model identity must be correlated rather than conflated. A direct OpenAI Image API response exposes `usage.input_tokens_details.text_tokens`, `image_tokens`, and image `output_tokens`, while the direct request supplies the image model; the final streaming completion carries usage once. A Responses API image-tool flow separately identifies the mainline response model/usage, but the documented `image_generation_call` lets the tool select its own GPT Image model and does not establish an exact child model/usage envelope. YOLOmux should record the mainline event, record the child tool relationship, and leave the opaque child visibly unestimated unless structured tool metadata supplies both model and usage—never charge it as the parent model or infer a default.

Subagent attribution is also structural. Codex child rollouts carry durable parent thread IDs and their own turn-context model/effort/usage; Claude descendants live in the parent transcript's subagent family and carry their own assistant model/usage. YOLOmux-owned YO!agent and AI Summary calls may expose structured usage directly at their invocation owners even when no durable agent transcript exists. Normalization must preserve root/agent/thread/parent/depth plus request/tool-call identity, emit auxiliary-call usage before UI formatting/discard, deduplicate family aggregates and completed/cumulative repetitions, and sum only at the displayed-range boundary. The Cost summary therefore adds a `By agent/source` tree alongside token-class and exact-model breakdowns so users can see which root agent, descendant, model-backed tool, or YOLOmux auxiliary model produced each observable subtotal.

For fully covered text usage, the estimate is `uncached_input × input_rate + cached_read × cached_read_rate + cache_write_5m × cache_write_5m_rate + cache_write_1h × cache_write_1h_rate + output × output_rate`, converted from per-million-token rates and summed across the displayed interval; image/audio/per-request atoms use their own catalog unit. The summary body first breaks this into token-class/modality rows, then exact-model rows, then a root/subagent/tool execution tree, and finishes with an `All models` row that exactly equals the unrounded heading amount. A missing category, unknown model, opaque model-backed tool, unrepresented provider modifier, or uncertain model alias produces `est. ≥$…` plus visible `Not estimated` usage rather than a confident total; no priceable usage produces `est. —`.

The first upgraded stats owner backfills every bucket in the full retained 24-hour window from surviving raw transcript families, including model, effort, token classes, pricing context, and completeness. The migration is single-owner, resumable, idempotent, and coordinated with live high-water offsets; missing transcripts preserve their output totals and mark those spans partial. Price remains a derived effective-dated view, so every catalog refresh immediately recomputes previous retained usage without rewriting token counts or applying today's price to yesterday's events.

### Delivery sequence and proof

1. Add the local catalog database, packaged seed, observed OpenAI/Anthropic/Gemini rows, source metadata, immutable effective-dated revisions, overrides, and isolated bootstrap/reconciliation tests.
2. Extend transcript/provider normalization and durable stats records with billable atoms, modalities/units, exact model evidence, root/subagent/tool attribution, effort/pricing context, and completeness; backfill the full retained history with crash/resume/concurrency/high-water tests.
3. Render the opt-in displayed-range Cost summary with token-class/modality, exact-model, execution-source, and All models breakdowns; add full/partial/unpriced states, source arithmetic, responsive/accessibility coverage, and negative proof that no USD-per-minute plot exists.
4. Add the admin-only source refresh/review workflow. Test stale source, changed table, malformed response, disagreement, accepted activation, restart recovery, and every retained displayed range using the effective revision at each event timestamp.

Completion evidence is a deterministic fixture whose text/image input/output/cache, per-model, root/subagent/tool, and All models totals match hand-worked arithmetic and reconcile to `Cost summary (est. $…, Σ displayed)`; a browser test that changes range/zoom/history readiness and proves the summary uses the exact same displayed bucket set; an old-schema migration/repricing test covering all recoverable retained history; and an audit test proving opaque/unknown/partial usage can never be attributed to the parent or shown as a confident total.
