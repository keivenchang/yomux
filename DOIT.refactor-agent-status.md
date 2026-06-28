# DOIT: Agent Status Refactor Audit

- [ ] X1 — Stop using icon-size as a topbar status-ball size proxy.
  **Pattern:** `static_src/css/yolomux/10_topbar_menus.css:659` defines `.topbar-activity-ball.agent-window-activity`, then sets `--agent-window-icon-size: var(--agent-status-ball-size-base)` and repeats `min-width`/`min-height` from the same base ball token. `docs/specs/GUI.md:95` says status-ball size has one parent, `--agent-status-ball-size`, and `--agent-window-icon-size` belongs to the static Claude/Codex symbol only.
  **Fix:** ladder rung 1, reuse the existing `.agent-window-activity` ball-size parent instead of overriding the icon token. Shape:
  ```css
  .topbar-activity-ball.agent-window-activity {
    --agent-status-ball-size: var(--agent-status-ball-size-base);
    width: var(--agent-status-ball-size);
    min-width: var(--agent-status-ball-size);
    min-height: var(--agent-status-ball-size);
  }
  ```
  Replace at: `static_src/css/yolomux/10_topbar_menus.css:659`; update the source guard in `tests/layout_restore.test.js:1630` so it proves topbar balls use `--agent-status-ball-size`, not `--agent-window-icon-size`.
  **Why:** the current CSS passes visually but violates the status-ball ownership contract. If icon sizing changes for Claude/Codex symbols later, the topbar status ball can drift because it is tied to the wrong token.

- [ ] X2 — Give YO!stats agent-status series keys one named owner.
  **Pattern:** the same four status series keys are duplicated in `static_src/js/yolomux/83_debug_panel.js:71` through `:74`, the activity chart group at `static_src/js/yolomux/83_debug_panel.js:84`, the legend override at `static_src/js/yolomux/83_debug_panel.js:84`, the bucket value switch at `static_src/js/yolomux/83_debug_panel.js:938` through `:941`, the sample-presence guard at `static_src/js/yolomux/83_debug_panel.js:956`, and several tests such as `tests/editor_preview.test.js:2094`, `:2510`, and `:2517` through `:2524`.
  **Fix:** ladder rung 3, create and migrate to named arrays/maps so plot order and legend order are explicit children of one status-series definition. Shape:
  ```javascript
  const jsDebugAgentStatusSeriesKeys = Object.freeze(['askAgents', 'workingAgents', 'transitionAgents', 'idleAgents']);
  const jsDebugAgentStatusLegendSeriesKeys = Object.freeze(['workingAgents', 'askAgents', 'transitionAgents', 'idleAgents']);
  const jsDebugAgentStatusBucketValueGetters = Object.freeze({
    askAgents: bucket => bucket.agentActivitySamples ? bucket.askAgentTotal / bucket.agentActivitySamples : 0,
    workingAgents: bucket => bucket.agentActivitySamples ? bucket.runAgentTotal / bucket.agentActivitySamples : 0,
    transitionAgents: bucket => bucket.agentActivitySamples ? bucket.transitionAgentTotal / bucket.agentActivitySamples : 0,
    idleAgents: bucket => bucket.agentActivitySamples ? bucket.idleAgentTotal / bucket.agentActivitySamples : 0,
  });
  ```
  Replace at: `static_src/js/yolomux/83_debug_panel.js:71`, `:84`, `:938`, `:956`, and the direct test arrays in `tests/editor_preview.test.js`.
  **Why:** the new green/red/yellow/idle legend order was implemented as another array beside the plot-order array. Without a named owner, the next status-series edit must update several string lists by hand and can silently break chart visibility, stacking, legend order, or tests independently.

- [ ] X3 — Route topbar status-ball class selection through the shared agent-window tone helper.
  **Pattern:** `static_src/js/yolomux/45_agent_window_activity.js:57` owns `agentWindowActivityTone(state)`, and `static_src/js/yolomux/45_agent_window_activity.js:676` through `:701` owns `agentWindowStatusDotHtml()` class/tone wiring for red/yellow/green balls. The topbar helper in `static_src/js/yolomux/20_layout_state.js:2067` through `:2074` manually maps `working`/`attention`/`cooldown` to `agent-window-activity--working`/`--attention`/`--cooldown` and separately calls `statusIndicatorDotClasses()`.
  **Fix:** ladder rung 2, extend the existing shared status-dot path with a small options shape for status-only/count surfaces, or extract a shared `agentWindowActivityToneWrapperClass(tone)` helper used by both the topbar and agent-window renderer. Shape:
  ```javascript
  function agentWindowActivityToneWrapperClass(tone) {
    const normalizedTone = agentWindowActivityTone(tone);
    return ['attention', 'cooldown', STATE_KEY.working].includes(normalizedTone)
      ? `agent-window-activity--${agentWindowStatusToneClass(normalizedTone)}`
      : '';
  }
  ```
  Replace at: `static_src/js/yolomux/20_layout_state.js:2067` and the relevant class construction in `static_src/js/yolomux/45_agent_window_activity.js`.
  **Why:** topbar balls currently reuse the status-dot CSS, but the wrapper tone class is a parallel mapping. If the status tones gain another class, urgency rule, or naming tweak, the topbar can drift from tabs/window buttons even though they are meant to look identical.
