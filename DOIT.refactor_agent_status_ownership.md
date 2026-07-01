# Refactor agent-status ownership

Scope: Audit the agent-status source, classifier, renderer, animation, Preferences example, keyboard legend, topbar count balls, and sub-window CSS after `bf9e81f9`. Broad checks found no duplicate JavaScript function definitions and no round-number backend refresh interval violation; the items below are the concrete duplicated owners found in this path.

- [ ] R1 — Give visible status tones one classifier owner.
  **Pattern:** `static_src/js/yolomux/45_agent_window_activity.js:322`, `:331`, `:916`, `:921`, `:935`, `:936`, `:939`, `:943`, `:946`, and `:978` repeat ten arrays containing working, attention, and cooldown in different orders, with one site adding acknowledged; a new tone or renamed state can reach selection but miss aggregation, shape retention, pulse, wrapper class, or style generation.
  **Pipeline:** Source owner is `sessionAgentWindowStatusModel`; classifier owners are currently split between `agentWindowActivityTone`, `agentWindowStatusToneForItem`, and the ten inline membership arrays; renderer owners are `agentWindowStatusDotHtml` and `agentWindowActivityStyleAttribute`; consumers are pane Tabs, window buttons, Tabber, YO!info, popovers, topbar counts, keyboard help, and Preferences.
  **Fix:** Resolution ladder rung 3: create one ordered tone definition plus one membership helper in `45_agent_window_activity.js`, migrate all ten inline arrays in the same change, and add an invariant test that every classifier/renderer accepts exactly the shared set while acknowledgement remains an overlay rather than a fourth shape.
  ```javascript
  const AGENT_WINDOW_VISIBLE_TONES = Object.freeze([STATE_KEY.working, 'attention', 'cooldown']);
  const AGENT_WINDOW_AGGREGATE_TONES = Object.freeze(['attention', 'cooldown', STATE_KEY.working]);

  function agentWindowVisibleTone(value) {
    return AGENT_WINDOW_VISIBLE_TONES.includes(value);
  }
  ```
  **Why:** One state-list edit must update selection, aggregation, animation, and shape rendering together; the current copies can recreate the exact half-rendered status bugs this subsystem has already had.

- [ ] R2 — Mark sub-window activity once instead of repeating three surface selectors 60 times.
  **Pattern:** `static_src/css/yolomux/40_layout_panes_tabs.css:1840-2018` repeats `.tmux-window-button`, `.session-agent-row`, and the Tabber window-label selector 60 times across base geometry, animation reset, tone fills, play/stop/pause pseudo-elements, offsets, pulse, and acknowledgement; adding a fourth sub-window surface requires copying every rule correctly.
  **Pipeline:** Source owner is the status item returned by `agentWindowActivityIconForStatusItem`; classifier owner is the `subwindowGlyphPulse` decision in `agentWindowActivityIconHtml`; renderer owner is the same activity wrapper, but CSS independently reclassifies the wrapper by ancestor surface; consumers are the tmux window bar, YO!info/session rows, and Tabber window rows.
  **Fix:** Resolution ladder rung 2: extend `agentWindowActivityIconHtml` to emit one `agent-window-activity--subwindow` modifier whenever it renders the play/stop/pause form, replace the 60 ancestor copies with that parent class, and keep only the genuinely active/current ancestor overrides as small variants.
  ```javascript
  const wrapperClasses = [
    'agent-window-activity',
    subwindowGlyph ? 'agent-window-activity--subwindow' : '',
    // existing tone and acknowledgement modifiers
  ];
  ```
  **Why:** The present selector fan-out already made gray fills and pseudo-element shapes diverge; one renderer-owned class makes all current and future sub-window surfaces inherit the same geometry and animation automatically.

- [ ] R3 — Route keyboard help and topbar balls through the live status-dot renderer.
  **Pattern:** Live panes use `agentWindowStatusDotHtml` at `static_src/js/yolomux/45_agent_window_activity.js:926-970`, but `topbarActivityCountBallHtml` hand-builds the same dot at `static_src/js/yolomux/20_layout_state.js:2178-2187`, `keyboardLegendStatusSample` hand-builds both ball and glyph forms at `:2656-2665`, and Preferences constructs a parallel sample item at `static_src/js/yolomux/82_preferences_panel.js:166-197`; the Preferences path also duplicates hard-coded English labels at `:170` and `:183` inside localized UI.
  **Pipeline:** Runtime source owner is `sessionAgentWindowStatusModel`; runtime classifier owners are `agentWindowActivityIconForStatusItem` and `agentWindowStatusToneForItem`; live renderer owner is `agentWindowStatusDotHtml`; topbar counts consume the shared model but bypass the renderer, while keyboard help and Preferences use static sample sources and separate builders.
  **Fix:** Resolution ladder rung 2: extend `agentWindowStatusDotHtml` with a small tone/sample adapter that accepts `surface`, `pulse`, `acknowledging`, and `label`, move the sample-item builder beside the live renderer, route topbar, keyboard help, and Preferences through it, and use translation keys for sample labels.
  ```javascript
  function agentWindowStatusDotHtmlForTone(tone, options = {}) {
    return agentWindowStatusDotHtml(agentWindowStatusSampleItem(tone, options), options);
  }
  ```
  **Why:** CSS reuse alone does not protect markup, modifiers, acknowledgement shape retention, accessibility text, or future tone changes; every user-facing example and summary should inherit those changes from the same renderer.

- [ ] R4 — Tokenize status-ring RGB values and remove the stale cooldown tuple.
  **Pattern:** `255 51 71` occurs ten times across `static_src/css/yolomux/20_sessions_popovers.css:199`, `:293`, `:371-377` and `static_src/css/yolomux/50_terminal_file_tree.css:118-119`; `static_src/css/yolomux/20_sessions_popovers.css:296` separately hard-codes cooldown as `245 197 66` even though `static_src/css/yolomux/00_tokens_base.css:68` owns the current dark value `255 214 51` and `:627` owns the light-theme value `194 138 0`.
  **Pipeline:** Source owner should be the theme token sheet; status classes classify working/attention/cooldown; attention-ring keyframes and file-tree attention rows render the RGB value; every theme and every status surface consumes the resulting custom property.
  **Fix:** Resolution ladder rung 2: extend `00_tokens_base.css` with named working and attention ring RGB tokens, reuse the existing `--agent-status-cooldown-rgb`, and replace every literal/fallback with those owners in the same change.
  ```css
  :root {
    --agent-status-working-rgb: 82 210 115;
    --agent-status-attention-ring-rgb: 255 51 71;
  }

  .agent-window-activity--cooldown {
    --attention-ring-rgb: var(--agent-status-cooldown-rgb);
  }
  ```
  **Why:** The stale cooldown tuple already disagrees with both theme values, and changing the attention ring currently requires editing ten copies across two files.
