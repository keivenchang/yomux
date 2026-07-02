const {
  assert,
  fs,
  UI_PINS,
  vm,
  FILE_EXPLORER_OPEN_INTENT_STORAGE_KEY_FOR_TEST,
  DEFAULT_TEST_SETTINGS,
  TestClassList,
  TestStyle,
  testDatasetKeyForAttribute,
  TestElement,
  TestFile,
  TestFormData,
  assertNoStandalonePrBadge,
  assertSingleCiBadge,
  loadYolomux,
  fileExplorerClosedOptions,
  loadYolomuxWithFileExplorerClosed,
  treeKeyEvent,
  tabElement,
  tabStrip,
  dragEvent,
  fileDragEvent,
  jsonResponse,
  flushAsyncWork,
  terminalLine,
  nestedSlots,
  parseUrl,
  canonical,
  makeFileTree,
  test,
  testAsync,
  runSuites,
  finishSuite,
} = require('./layout_test_helper');

const DEBUG_AGENT_STATUS_SERIES = ['askAgents', 'workingAgents', 'transitionAgents', 'idleAgents'];
const DEBUG_AGENT_STATUS_LEGEND_SERIES = ['workingAgents', 'askAgents', 'transitionAgents', 'idleAgents'];

function tmuxWindowButtonElement(session, index, active = false) {
  const button = new TestElement(`tmux-window-${session}-${index}`, 'button');
  button.className = `tab tmux-window-button${active ? ' active' : ''}`;
  button.dataset.windowIndex = String(index);
  button.dataset.windowSession = session;
  button.setAttribute('aria-pressed', active ? 'true' : 'false');
  return button;
}

function tmuxWindowBarElement(session, buttons) {
  const bar = new TestElement(`tmux-window-bar-${session}`);
  bar.dataset.tmuxWindowBar = session;
  buttons.forEach(button => bar.appendChild(button));
  return bar;
}

function activeTmuxWindowIndexesFromHtml(html) {
  return [...String(html || '').matchAll(/<button\b([^>]*)>/g)]
    .filter(([, attrs]) => /\btmux-window-button\b/.test(attrs) && /\bactive\b/.test(attrs))
    .map(([, attrs]) => attrs.match(/\bdata-window-index="([^"]+)"/)?.[1] || '');
}

function activeTmuxWindowIndexesFromElement(root) {
  return Array.from(root.querySelectorAll('.tmux-window-button[data-window-index]'))
    .filter(button => button.classList.contains('active'))
    .map(button => button.dataset.windowIndex || '');
}

function tmuxWindowButtonFromElement(root, index) {
  return root.querySelector(`.tmux-window-button[data-window-index="${String(index)}"]`);
}

async function runEditorPreviewSuite() {
  test('search history pane renders search results and compact runs', () => {
    const api = loadYolomux('', ['1']);
    api.setSearchHistoryStateForTest(
      'beta',
      {
        query: 'beta',
        results: [{
          session: '1',
          timestamp: '2026-01-01T00:00:00Z',
          kind: 'state_changed',
          source: 'event',
          title: 'beta event',
          snippet: 'beta event detail',
          target: {type: 'events', session: '1', tab: 'events'},
        }],
      },
      {
        runs: [{
          session: '1',
          prompt: 'please ship beta rollout',
          cwd: '/home/test/project',
          final_state: 'done',
          latest_summary: 'beta rollout finished',
          agent: {kind: 'codex', model: 'gpt-test'},
          pr: {number: 42, state: 'open'},
        }],
      }
    );

    const html = api.searchHistoryPanelHtmlForTest();

    assert.ok(html.includes('data-search-history-form'), 'Search & Runs pane includes a search form');
    assert.ok(html.includes('data-search-result-index="0"'), 'search result rows are actionable');
    assert.ok(html.includes('beta event detail'), 'search result snippets render');
    assert.ok(html.includes('data-run-history-session="1"'), 'run history rows are actionable');
    assert.ok(html.includes('please ship beta rollout'), 'run history prompts render');
    assert.ok(html.includes('beta rollout finished'), 'run history summaries render');
  });

  test('Markdown preview HTML bgcolor callouts adapt to editor theme', () => {
    const previewCss = fs.readFileSync('static_src/css/yolomux/60_editor_file_panels.css', 'utf8');
    const markdownSource = fs.readFileSync('static_src/js/yolomux/93_markdown_preview.js', 'utf8');
    const popoutSource = fs.readFileSync('static_src/js/yolomux/94_preview_popout.js', 'utf8');
    for (const source of [previewCss, popoutSource]) {
      assert.ok(source.includes('.markdown-html-light-bg'), 'light bgcolor callouts get scoped preview styling');
      assert.ok(source.includes('table[bgcolor]'), 'legacy bgcolor tables are styled even when existing preview DOM has no renderer-added class');
      assert.ok(source.includes('--markdown-strong: var(--markdown-html-light-text)'), 'light callouts force dark bold text instead of dark-theme text');
      assert.ok(source.includes('--code-inline: var(--markdown-html-light-code)'), 'yellow callouts reuse stable light inline-code ink');
      assert.ok(source.includes('border-color: transparent'), 'yellow borderless callouts do not inherit the dark table grid');
      assert.ok(source.includes('--markdown-html-dark-bg'), 'dark editor preview turns light bgcolor callouts into a dark highlight');
      assert.ok(source.includes('--markdown-html-dark-text'), 'dark bgcolor callout override keeps text readable on the dark highlight');
      assert.ok(source.includes('blockquote.markdown-alert-warning'), 'Markdown warning blockquotes use the same preview warning styling');
      assert.ok(/pre code\.hljs[\s\S]*padding:\s*0;/.test(source), 'highlight.js code blocks keep compact preview padding even when the CDN stylesheet loads later');
      assert.ok(/\.markdown-source-anchor\s*\{[\s\S]*display:\s*none;/.test(source), 'source-line anchors do not create blank line boxes inside alerts');
    }
    assert.ok(markdownSource.includes("const MARKDOWN_HTML_LIGHT_BG_CLASS = 'markdown-html-light-bg'"), 'renderer owns the shared light-bg class name');
    assert.ok(markdownSource.includes('MARKDOWN_ALERT_MARKER_RE'), 'renderer recognizes Markdown alert markers like [!WARNING]');
    assert.ok(markdownSource.includes("blockquote.classList.add('markdown-alert', `markdown-alert-${type}`)"), 'renderer classes Markdown alert blockquotes by alert type');
    assert.ok(markdownSource.includes('trimMarkdownCodeBlockEdgeNewlines(frag)'), 'renderer trims edge newlines from preview code blocks before highlighting');
    assert.ok(markdownSource.includes('removeMarkdownAlertLeadingBreaks(parent)'), 'renderer removes marker-only alert line breaks so callouts do not start with a blank row');
    assert.ok(markdownSource.includes("root.querySelectorAll('table[bgcolor], th[bgcolor], td[bgcolor]')"), 'renderer inspects legacy HTML bgcolor tables and cells');
    assert.ok(/markdownPreviewBgcolorIsLight\(value\)[\s\S]*>= 0\.58/.test(markdownSource), 'renderer only marks light bgcolor surfaces');
  });

  test('Markdown preview code blocks use grayer dark background while light mode keeps light panels', () => {
    const tokensCss = fs.readFileSync('static_src/css/yolomux/00_tokens_base.css', 'utf8');
    const previewCss = fs.readFileSync('static_src/css/yolomux/60_editor_file_panels.css', 'utf8');
    const popoutSource = fs.readFileSync('static_src/js/yolomux/94_preview_popout.js', 'utf8');
    assert.ok(tokensCss.includes('--markdown-preview-bg: #000000;'), 'dark Markdown Preview surface is pitch black');
    assert.ok(tokensCss.includes('--markdown-code-block-bg: #2a303b;'), 'dark code-block background uses a visibly lighter neutral block surface');
    assert.ok(/body:not\(\.editor-theme-light\) \.file-editor-content \.file-editor-preview-pane\.markdown-body,[\s\S]*\.file-editor-preview-pane-panel\.markdown-body\s*\{[\s\S]*background:\s*var\(--markdown-preview-bg\);/.test(previewCss), 'dark file-editor Markdown Preview panes use the pitch-black preview token');
    assert.ok(previewCss.includes('.markdown-body pre { background: var(--markdown-code-block-bg);'), 'Markdown Preview code blocks use the shared dark background token');
    assert.ok(/body\.editor-theme-light \.file-editor-content \.markdown-body pre\s*\{[\s\S]*background:\s*var\(--lt-panel\);/.test(previewCss), 'light editor preview keeps the existing light code-block background');
    assert.ok(popoutSource.includes('.file-preview-popout-window:not(.editor-theme-light) .markdown-body'), 'dark preview pop-outs use the same Markdown surface override');
    assert.ok(popoutSource.includes("'--markdown-preview-bg', '--markdown-code-block-bg'"), 'preview pop-outs copy Markdown preview surface tokens');
    assert.ok(popoutSource.includes("'--markdown-html-dark-bg', '--markdown-html-dark-border', '--markdown-html-dark-text'"), 'preview pop-outs copy dark callout tokens');
  });

  test('Finder/Differ/Tabber recency brightness and pulse apply in Ago and Date modes', () => {
    const api = loadYolomux('', ['1']);
    const nowMs = 2_000_000;
    const nowSeconds = nowMs / 1000;
    const entries = [
      {name: 'just.md', kind: 'file', mtime: nowSeconds - 5},
      {name: 'hot.md', kind: 'file', mtime: nowSeconds - 30},
      {name: 'fresh.md', kind: 'file', mtime: nowSeconds - 4 * 60},
      {name: 'ten.md', kind: 'file', mtime: nowSeconds - 9 * 60},
      {name: 'hour.md', kind: 'file', mtime: nowSeconds - 50 * 60},
      {name: 'old.md', kind: 'file', mtime: nowSeconds - 3 * 24 * 60 * 60},
    ];
    const rowMap = tree => Object.fromEntries(tree.querySelectorAll('.file-tree-row[data-path]').map(row => [row.dataset.path, row]));
    const dateCell = row => row.querySelector(':scope > .file-tree-date');
    const tokensCss = fs.readFileSync('static_src/css/yolomux/00_tokens_base.css', 'utf8');
    const sessionsCss = fs.readFileSync('static_src/css/yolomux/20_sessions_popovers.css', 'utf8');
    const paneTabsCss = fs.readFileSync('static_src/css/yolomux/40_layout_panes_tabs.css', 'utf8');
    const treeCss = fs.readFileSync('static_src/css/yolomux/50_terminal_file_tree.css', 'utf8');
    const changesCss = fs.readFileSync('static_src/css/yolomux/60_editor_file_panels.css', 'utf8');
    const bootstrapSource = fs.readFileSync('static_src/js/yolomux/00_bootstrap_state.js', 'utf8');
    const layoutSource = fs.readFileSync('static_src/js/yolomux/20_layout_state.js', 'utf8');
    const settingsRuntimeSource = fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8');
    const activitySource = fs.readFileSync('static_src/js/yolomux/45_agent_window_activity.js', 'utf8');
    const preferencesSource = fs.readFileSync('static_src/js/yolomux/82_preferences_panel.js', 'utf8');
    const popoverSource = fs.readFileSync('static_src/js/yolomux/60_popovers_tabs.js', 'utf8');
    const dotBlock = sessionsCss.match(/\.status-indicator--dot\s*\{[^}]*\}/)?.[0] || '';
    assert.ok(/function statusPulseAnimationEnabled\(\)\s*\{\s*return typeof globalThis !== 'undefined' && globalThis\.yolomuxEnableBroadStatusPulse === true;\s*\}/.test(layoutSource), 'continuous broad status pulse is disabled by default through one shared helper');
    assert.ok(/function attentionAnimationStyle\(now = Date\.now\(\), durationMs = agentStatusPulsePeriodMs, property = attentionAnimationDelayProperty\)[\s\S]*const value = attentionAnimationDelay\(now, durationMs\)/.test(layoutSource), 'new status markup stamps the current wall-clock phase instead of reusing a stale root delay');
    assert.ok(/function statusIndicatorToneClasses\(tone, options = \{\}\)[\s\S]*const pulseEnabled = options\.pulse !== false && statusPulseAnimationEnabled\(\)[\s\S]*tone === STATE_KEY\.working[\s\S]*status-indicator--working', pulseEnabled \? 'heartbeat-pulse'[\s\S]*tone === 'cooldown'[\s\S]*status-indicator--cooldown', pulseEnabled \? 'heartbeat-pulse'[\s\S]*pulseEnabled \? 'attention-pulse'[\s\S]*tone === 'attention'[\s\S]*status-indicator--attention', pulseEnabled \? 'heartbeat-pulse'[\s\S]*pulseEnabled \? 'attention-pulse'[\s\S]*tone === 'active'[\s\S]*status-indicator--active'[\s\S]*tone === 'settled'[\s\S]*status-indicator--settled[\s\S]*tone === STATE_KEY\.idle[\s\S]*status-indicator--idle/.test(layoutSource), 'attention/activity-dot status tones are centralized and can omit pulse classes per rendered item');
    assert.ok(/function updateTopbarActivityStatus\(\)[\s\S]*node\.innerHTML = html[\s\S]*scheduleAgentWindowActivityAnimationSync\(node\)/.test(layoutSource), 'topbar attention rerenders explicitly resync the shared attention animation phase');
    assert.ok(/function topbarActivityCountBallHtml\(count, tone[\s\S]*agentWindowStatusDotHtmlForTone\(tone, \{surface: 'topbar', pulse: false\}\)[\s\S]*statusIndicatorInlineClasses\('',\s*'topbar-activity-count'/.test(layoutSource), 'topbar activity counts route their ball through the live status-dot renderer');
    assert.ok(/function createTopbarActivityStatus\(\)[\s\S]*button\.onclick = \(\) => openTabberActivityOverview\(\)/.test(layoutSource), 'the topbar AI activity summary opens Tabber instead of YO!agent');
    assert.ok(/statusIndicatorTextClasses\(tone,\s*classes\)/.test(layoutSource), 'text status badges inherit shared text status behavior');
    assert.ok(/function statusIndicatorLabelClasses\(tone,\s*\.\.\.classes\)[\s\S]*statusIndicatorModifiedClasses\('status-indicator--label'/.test(layoutSource), 'attention status labels inherit shared status-indicator tone behavior without badge text-transform');
    assert.ok(/function agentWindowStatusDotHtml\(item, options = \{\}\)[\s\S]*const tone = agentWindowStatusToneForItem\(item\)[\s\S]*statusIndicatorDotClasses\(\s*tone,[\s\S]*'agent-window-status-dot'/.test(activitySource), 'tmux sub-window status dots inherit shared dot behavior through the shared activity-tone helper');
    assert.ok(/sessionPopoverAgentWindowRowHtml\(agent[\s\S]*agentWindowActivityIconHtmlForStatus\(agent, agent\.kind[\s\S]*statusBeforeAgent: true/.test(popoverSource), 'session popover agent rows reuse the shared state-then-agent renderer ordering');
    assert.ok(/session-agent-kind">\$\{activityHtml\}<\/span>\$\{esc\(label\)\}/.test(popoverSource), 'session popover keeps state/AI controls inline with the window text instead of wrapping the full label in a fixed control group');
    assert.ok(/function refreshAgentWindowActivityDisplays\(\)[\s\S]*renderPanels\(activePaneItems\(\), \{reason: 'agent-window-activity'\}\);[\s\S]*renderPaneTabStrips\(\)/.test(activitySource), 'sub-window activity refreshes redraw parent pane tab strips immediately');
    assert.ok(/mutationTouchesAgentWindowActivity\(mutation\)[\s\S]*status-indicator[\s\S]*heartbeat-pulse[\s\S]*querySelector\?\.\('\.status-indicator\.heartbeat-pulse, \.status-indicator\.attention-pulse'/.test(activitySource), 'the shared animation sync observer watches attention status indicators as well as agent activity wrappers');
    assert.ok(/function syncAgentWindowPulseAnimationCurrentTime\(node, nowMs = Date\.now\(\)\)[\s\S]*animation\.currentTime = Number\(nowMs\) \|\| 0/.test(activitySource), 'red/yellow/green attention balls are phase-synced from one sampled timeline value');
    assert.ok(/function syncAgentWindowActivityAnimationDelays\(root = document\)[\s\S]*agentWindowActivityPulseSelector[\s\S]*attentionAnimationClockDelay\(nowMs\)[\s\S]*localDelay && localDelay !== delay[\s\S]*node\.style\.removeProperty\('--attention-animation-delay'\)[\s\S]*syncAgentWindowPulseAnimationCurrentTime\(node, nowMs\)/.test(activitySource), 'red/yellow/green attention balls share one root delay while stale local delays are cleared instead of rewritten');
    assert.ok(/function ensureAgentWindowActivityMutationObserver\(\)[\s\S]*!statusPulseAnimationEnabled\(\)[\s\S]*return[\s\S]*observe\(document\.body, \{childList: true, subtree: true\}/.test(activitySource) && /function disconnectAgentWindowActivityMutationObserver\(\)[\s\S]*disconnect\?\.\(\)[\s\S]*agentWindowActivityMutationObserver = null/.test(activitySource) && /function scheduleAgentWindowActivityAnimationSync\(root = document\)[\s\S]*!statusPulseAnimationEnabled\(\)[\s\S]*disconnectAgentWindowActivityMutationObserver\(\)[\s\S]*ensureAgentWindowActivityMutationObserver\(\)[\s\S]*syncAgentWindowActivityAnimationDelays\(root\)/.test(activitySource), 'agent-window animation phase sync keeps explicit render-path sync while installing the body mutation observer only when status pulsing is enabled');
    assert.ok(/\.status-indicator\s*\{[^}]*display:\s*inline-flex/.test(sessionsCss), 'attention/activity-dot markers share the status-indicator parent');
    assert.ok(/\.status-indicator--text\s*\{[^}]*border:\s*1px solid var\(--divider\)/.test(sessionsCss), 'text status badges inherit pill framing from the shared parent modifier');
    assert.ok(/width:\s*1em/.test(dotBlock) && /min-width:\s*1em/.test(dotBlock) && /color:\s*var\(--muted\)/.test(dotBlock) && /font-size:\s*0\.9em/.test(dotBlock), 'status ball markers keep the compact glyph-dot style from the shared parent modifier');
    assert.ok(/\.heartbeat-pulse\s*\{[^}]*animation-duration:\s*var\(--pulse-duration\)[^}]*animation-delay:\s*var\(--attention-animation-delay, 0s\)[^}]*animation-timing-function:\s*var\(--status-pulse-timing\)[^}]*animation-iteration-count:\s*infinite[^}]*animation-direction:\s*normal/.test(sessionsCss), 'heartbeat indicators share one stepped pulse cadence parent');
    assert.ok(/\.status-indicator--dot\s*\{[^}]*border-radius:\s*999px[\s\S]*opacity:\s*1/.test(sessionsCss), 'circle status markers retain their shared round footprint before the pulse applies opacity');
    assert.equal(/status-ball-size-pulse|--status-dot-rest-scale|--status-dot-peak-scale|--status-dot-size/.test(sessionsCss), false, 'status balls do not pulse by changing geometry or use the filled-disc size path');
    assert.ok(/\.agent-window-activity\s*\{[\s\S]*--agent-status-ball-border:\s*#000/.test(sessionsCss), 'one shared owner defines the thin black status-ball border');
    assert.ok(/\.session-agent-activity-marker \.agent-window-status-dot\s*\{[\s\S]*inline-size:\s*var\(--agent-status-ball-size\)[\s\S]*block-size:\s*var\(--agent-status-ball-size\)[\s\S]*flex:\s*0 0 var\(--agent-status-ball-size\)[\s\S]*background:\s*var\(--agent-status-ball-fill, currentColor\)[\s\S]*border:\s*1px solid var\(--agent-status-ball-border\)[\s\S]*color:\s*transparent[\s\S]*filter:\s*none/.test(sessionsCss), 'session status balls are square-footprint filled discs with a thin black border and no glow filter');
    assert.equal(/display:\s*inline-grid/.test(dotBlock), false, 'status balls are not drawn as fixed inline-grid discs');
    assert.equal(/font-size:\s*0\s*;/.test(dotBlock), false, 'status balls keep a visible glyph font size');
    assert.ok(/\.status-indicator--dot\.status-indicator--working\.heartbeat-pulse\s*\{[\s\S]*animation-name:\s*agent-status-opacity-pulse/.test(sessionsCss), 'working balls use the shared opacity pulse instead of a color flash and glow');
    assert.ok(/--agent-status-opacity-subtle-min:\s*0\.16[\s\S]*--agent-status-pulse-min-opacity:\s*var\(--agent-status-opacity-subtle-min\)/.test(tokensCss), 'status opacity pulses inherit one shared subtle minimum token');
    assert.equal(/status-indicator--working:not\(\.agent-window-status-dot--segmented\)[\s\S]*--agent-status-pulse-min-opacity/.test(sessionsCss), false, 'full-green aggregate Tab circles do not override the shared subtle pulse range');
    assert.ok(/\.status-indicator--dot\.status-indicator--cooldown\.heartbeat-pulse\s*\{[\s\S]*animation-name:\s*agent-status-opacity-pulse/.test(sessionsCss), 'yellow cooldown balls use the same opacity pulse as working balls');
    assert.ok(/@keyframes agent-status-opacity-pulse\s*\{[\s\S]*opacity:\s*var\(--agent-status-pulse-min-opacity\)[\s\S]*opacity:\s*1/.test(sessionsCss), 'one shared keyframe inherits the status opacity range');
    assert.equal(/working-ball-hard-flash/.test(sessionsCss), false, 'working ball color-flash keyframes are removed');
    assert.equal(/#a9ff7a/.test(sessionsCss), false, 'working balls do not peak into the old yellow-lime tone');
    assert.ok(/\.agent-window-agent-icon--active\s*\{[^}]*animation-name:\s*agent-symbol-glow-cadence/.test(sessionsCss), 'the --active agent glyph keeps the glow-cadence; status states use a static symbol plus an opacity-pulsed ball');
    assert.ok(/\.agent-window-activity\s*\{[\s\S]*display:\s*inline-flex[\s\S]*gap:\s*2px/.test(sessionsCss), 'agent status symbols and balls render side by side through the shared inline-flex activity wrapper');
    assert.ok(/\.pane-tab-core\s*\{[\s\S]*gap:\s*2px/.test(paneTabsCss), 'session tab chrome keeps each leading icon only 2px from the next item');
    assert.ok(/\.session-button-prefix\s*\{[\s\S]*gap:\s*0/.test(sessionsCss), 'the shared session prefix keeps the identifier tight to its metadata group without a wasted gap');
    assert.ok(/\.pane-tab \.session-button-text\s*\{[\s\S]*gap:\s*1px/.test(paneTabsCss), 'tab metadata and description use the compact 1px content gap');
    assert.ok(/\.tmux-window-button\s*\{[\s\S]*padding-inline:\s*2px/.test(paneTabsCss), 'sub-window buttons use compact horizontal padding');
    assert.ok(/\.tmux-window-button \.tmux-window-name-label\s*\{[\s\S]*gap:\s*1px/.test(paneTabsCss), 'sub-window agent identity and labels share a visible one-pixel minimum gap');
    assert.ok(/\.agent-window-activity--subwindow\s*\{[\s\S]*--subwindow-status-slot-size:\s*calc\(var\(--agent-status-ball-size\) \* var\(--subwindow-status-glyph-scale\)\)/.test(paneTabsCss) && /\.tmux-window-button \.agent-window-activity--subwindow\s*\{[\s\S]*gap:\s*3px/.test(paneTabsCss) && /\.tmux-window-button \.agent-window-status-placeholder\s*\{[\s\S]*inline-size:\s*var\(--subwindow-status-slot-size\)[\s\S]*flex:\s*0 0 var\(--subwindow-status-slot-size\)/.test(paneTabsCss), 'sub-window state glyphs and invisible holders share one compact slot with a visible three-pixel icon gap');
    assert.ok(/\.agent-window-activity\s*\{[\s\S]*--agent-status-ball-size:\s*var\(--agent-status-ball-size-base\)/.test(sessionsCss), 'the shared activity wrapper owns the base agent status-ball size token');
    assert.ok(/\.agent-window-activity--subwindow\s*\{[\s\S]*--agent-status-ball-size:\s*var\(--agent-status-ball-size-base\)/.test(paneTabsCss), 'every sub-window surface uses the Tab status-ball size through the renderer-owned modifier');
    assert.ok(/--agent-status-working-rgb:\s*82 210 115[\s\S]*--agent-status-attention-ring-rgb:\s*255 51 71[\s\S]*--agent-status-cooldown:\s*#ffd633[\s\S]*--agent-status-cooldown-rgb:\s*255 214 51/.test(tokensCss), 'working, attention, and cooldown ring RGB values have one shared token owner');
    assert.ok(/\.agent-window-activity--subwindow \.agent-window-status-dot\s*\{[\s\S]*--subwindow-status-attention-fill:\s*var\(--danger-strong\)[\s\S]*--subwindow-status-cooldown-fill:\s*var\(--agent-status-cooldown\)[\s\S]*--subwindow-status-glyph-fill:\s*currentColor[\s\S]*--subwindow-status-glyph-border-color:[\s\S]*border-radius:\s*0[\s\S]*color:\s*transparent/.test(paneTabsCss), 'every sub-window status dot inherits the flat 66% play/stop/pause shape and border from one renderer-owned modifier');
    assert.ok(/\.agent-window-activity--subwindow \.agent-window-status-dot\.status-indicator--working\s*\{[\s\S]*--subwindow-status-glyph-fill:\s*var\(--pr-status-passing\)/.test(paneTabsCss), 'working sub-window glyph fill does not depend on hidden text currentColor');
    assert.ok(/\.agent-window-activity--subwindow \.agent-window-status-dot\.status-indicator--attention\s*\{[\s\S]*--subwindow-status-glyph-fill:\s*var\(--subwindow-status-attention-fill\)/.test(paneTabsCss), 'attention sub-window glyph fill uses the scoped saturated stop-red token, not hidden text currentColor');
    assert.ok(/\.agent-window-activity--subwindow \.agent-window-status-dot\.status-indicator--cooldown\s*\{[\s\S]*--subwindow-status-glyph-fill:\s*var\(--subwindow-status-cooldown-fill\)/.test(paneTabsCss), 'cooldown sub-window glyph fill uses the scoped yellow owner, not hidden text currentColor or global accent gold');
    assert.equal(/\.agent-window-activity--subwindow \.agent-window-status-dot\s*\{[^}]*(?:overflow:\s*hidden|text-indent:\s*-999px|border-radius:\s*max\(1px, calc\(var\(--agent-status-ball-size-base\) \* 0\.08\)\))/.test(paneTabsCss), false, 'sub-window status dot containers do not clip glyph glow into a rounded box');
    assert.equal(/--subwindow-status-glyph-scale:\s*0\.4/.test(paneTabsCss), false, 'sub-window glyphs never shrink to a stale 40% size');
    assert.ok(/\.agent-window-activity--subwindow \.agent-window-status-dot\.status-indicator--working\s*\{[\s\S]*--subwindow-status-glyph-fill:\s*var\(--pr-status-passing\)/.test(paneTabsCss), 'working/play sub-window glyphs stay vibrant green through the shared 66% scale');
    assert.ok(/agent-window-status-dot--acknowledging[\s\S]*--agent-status-ball-fill:\s*var\(--muted\)/.test(sessionsCss) && /agent-window-status-dot--acknowledging[\s\S]*--subwindow-status-glyph-fill:\s*var\(--muted\) !important/.test(paneTabsCss), 'acknowledging play/pause/stop glyphs and parent balls share the muted gray owner');
    assert.ok(/const acknowledgementShapeClass = acknowledging && agentWindowVisibleTone\(item\.state\)[\s\S]*`status-indicator--\$\{item\.state\}`[\s\S]*acknowledgementShapeClass/.test(activitySource), 'gray acknowledgement keeps the original play-triangle, stop-square, or pause-bar shape modifier');
    assert.ok(/function agentWindowAcknowledgementVisualDurationMs\(\)[\s\S]*attentionAnimationDurationMs\(agentStatusPulsePeriodMs\)/.test(activitySource) && /const durationMs = agentWindowAcknowledgementVisualDurationMs\(\)[\s\S]*setTimeout\([\s\S]*}, durationMs\)/.test(activitySource), 'gray acknowledgement lifetime comes from the configured status-ball pulse period');
    assert.ok(/@keyframes agent-status-acknowledgement-fade\s*\{[\s\S]*0%\s*\{\s*opacity:\s*1[\s\S]*100%\s*\{\s*opacity:\s*0/.test(sessionsCss) && /agent-window-status-dot--acknowledging[\s\S]*animation-name:\s*agent-status-acknowledgement-fade[\s\S]*animation-duration:\s*var\(--agent-status-acknowledgement-duration, var\(--pulse-duration\)\)/.test(sessionsCss), 'one shared one-way fade retires gray live and preview markers over the pulse period');
    assert.ok(/agentWindowAcknowledgementVisuals\.set\(key, \{startedAtMs, untilMs, durationMs, timer, acknowledgementKey, refreshed: options\.refresh !== false, agent: visualAgent\}\)/.test(activitySource) && /acknowledgementElapsedMs:\s*Math\.max\(0, Date\.now\(\) - acknowledgementVisual\.startedAtMs\)/.test(activitySource) && /--agent-status-acknowledgement-delay: \$\{-elapsedMs \/ 1000\}s/.test(activitySource), 'gray fade progress survives live window-bar rerenders instead of restarting from opaque');
    assert.ok(/function preferencesStatusPulseExampleHtml\(\)[\s\S]*const states = AGENT_WINDOW_VISIBLE_TONES[\s\S]*groupHtml\('tab'\)[\s\S]*groupHtml\('subwindow'\)[\s\S]*groupHtml\('acknowledgement'\)/.test(preferencesSource) && /function preferencesStatusPulseExampleMarkerHtml\(state, group\)[\s\S]*pref\.performance\.statusSample\.acknowledged[\s\S]*agentWindowStatusSampleItem\(state, sampleOptions\)[\s\S]*agentWindowStatusDotHtmlForTone\(state, sampleOptions\)/.test(preferencesSource), 'Preferences routes colored Tab balls, colored glyphs, and fading gray glyphs through the shared sample item and live dot renderer');
    assert.ok(/function keyboardLegendStatusSample\(kind, text = '●', options = \{\}\)[\s\S]*agentWindowVisibleTone\(kind\)[\s\S]*agentWindowStatusDotHtmlForTone\(kind,[\s\S]*surface: subwindow \? 'subwindow' : 'legend'/.test(layoutSource), 'keyboard shape and color samples route through the live status-dot renderer');
    assert.equal(/subwindowPulseActive:\s*true/.test(activitySource), false, 'working/play sub-window glyphs use the shared pulseActive path instead of a parallel forever-pulse override');
    assert.ok(/function agentWindowStatusDotHtml\(item, options = \{\}\)[\s\S]*const pulse = !acknowledging && animate && item\.pulseActive !== false;[\s\S]*const subwindowPulse = pulse;[\s\S]*options\.subwindowGlyphPulse === true && subwindowPulse[\s\S]*agent-window-status-dot--subwindow-pulse/.test(activitySource), 'the shared status dot renderer emits the sub-window pulse class from the shared pulseActive lifecycle and keeps acknowledgement gray static');
    assert.ok(/function agentWindowActivityIconHtml\(agentKey, state, idleSeconds, options = \{\}\)[\s\S]*const subwindowGlyphPulse = options\.subwindowGlyphPulse === true \|\| \(options\.subwindowGlyphPulse !== false && statusOnly !== true\)[\s\S]*subwindowGlyphPulse \? 'agent-window-activity--subwindow' : ''/.test(activitySource), 'the shared renderer marks every play/stop/pause wrapper once instead of making surfaces reclassify it');
    assert.ok(/\.agent-window-activity--subwindow \.agent-window-status-dot:is\([^)]*agent-window-status-dot--subwindow-pulse[^)]*\)\s*\{[\s\S]*box-shadow:\s*none !important[\s\S]*animation:\s*none !important/.test(paneTabsCss), 'sub-window pulse suppresses element-level box-shadow/animation so the square container never draws a ring');
    assert.ok(/\.agent-window-activity--subwindow \.agent-window-status-dot\.agent-window-status-dot--subwindow-pulse\s*\{[\s\S]*animation:\s*agent-status-opacity-pulse[\s\S]*!important/.test(paneTabsCss), 'sub-window glyphs animate the same status-dot opacity property and keyframe as their parent circles');
    const subwindowOwnerCss = paneTabsCss.slice(paneTabsCss.indexOf('.agent-window-activity--subwindow {'), paneTabsCss.indexOf('.tmux-window-bar[data-tmux-window-label-mode="names"]'));
    assert.equal(/(?:\.tmux-window-button|\.session-agent-row|\.file-tree-row\.tabber-row)[^,{]*\.agent-window-status-dot/.test(subwindowOwnerCss), false, 'sub-window geometry and animation no longer duplicate surface ancestor selectors');
    assert.equal(/subwindow-status-glyph-pulse|subwindow-status-glyph-outline-filter|subwindow-status-cooldown-outline-filter|drop-shadow/.test(subwindowOwnerCss), false, 'sub-window play/stop/pause glyphs use fill and borders instead of glow filters');
    assert.ok(/\.agent-window-activity--subwindow \.agent-window-status-dot::before,[\s\S]*\.agent-window-activity--subwindow \.agent-window-status-dot\.status-indicator--cooldown::after\s*\{[\s\S]*transform:\s*translate\(-50%, -50%\)/.test(paneTabsCss), 'sub-window glyph pseudo-elements use the centered base transform on all sub-window surfaces');
    assert.ok(/\.agent-window-activity--subwindow \.agent-window-status-dot\.status-indicator--working::before\s*\{[\s\S]*background:\s*var\(--subwindow-status-glyph-border-color\)[\s\S]*clip-path:\s*polygon/.test(paneTabsCss), 'working sub-window status draws the outer border of a centered CSS play triangle');
    assert.ok(/\.agent-window-activity--subwindow \.agent-window-status-dot\.status-indicator--working::after\s*\{[\s\S]*background:\s*var\(--subwindow-status-glyph-fill\)[\s\S]*clip-path:\s*polygon/.test(paneTabsCss), 'working sub-window status fills the bordered triangle with the shared green token');
    assert.ok(/\.agent-window-activity--subwindow \.agent-window-status-dot\.status-indicator--attention::before\s*\{[\s\S]*inline-size:\s*calc\(var\(--agent-status-ball-size\) \* var\(--subwindow-status-glyph-scale\)\)[\s\S]*background:\s*var\(--subwindow-status-glyph-fill, currentColor\)/.test(paneTabsCss), 'attention sub-window status uses a centered solid CSS stop square from the shared glyph-fill owner');
    assert.equal(/status-indicator--attention::before[\s\S]{0,520}border:\s*var\(--subwindow-status-glyph-border-width\)/.test(paneTabsCss), false, 'attention stop square does not stack a per-shape border on top of the shared outline');
    assert.ok(/\.agent-window-activity--subwindow \.agent-window-status-dot\.status-indicator--cooldown::before,[\s\S]*\.agent-window-activity--subwindow \.agent-window-status-dot\.status-indicator--cooldown::after\s*\{[\s\S]*--subwindow-status-pause-bar-offset:\s*calc\(var\(--agent-status-ball-size\) \* var\(--subwindow-status-glyph-scale\) \* 0\.31\)[\s\S]*inline-size:\s*calc\(var\(--agent-status-ball-size\) \* var\(--subwindow-status-glyph-scale\) \* 0\.16\)[\s\S]*background:\s*var\(--subwindow-status-glyph-fill, currentColor\)[\s\S]*filter:\s*none/.test(paneTabsCss), 'cooldown sub-window status uses widened paired pause bars and the same no-glow opacity mechanism');
    assert.equal(/status-indicator--cooldown::before[\s\S]{0,760}border:\s*var\(--subwindow-status-glyph-border-width\)/.test(paneTabsCss), false, 'cooldown pause bars do not stack per-bar borders on top of the shared outline');
    assert.equal(/translate\(-(?:42|72)%, -50%\)/.test(paneTabsCss), false, 'sub-window glyphs do not use side-biased transform fudges');
    assert.equal(/status-indicator--cooldown::before[\s\S]{0,520}box-shadow:/.test(paneTabsCss), false, 'cooldown pause bars do not fake the second bar with a box-shadow offset');
    assert.ok(/\.tmux-window-bar \.tmux-window-button\.active \.agent-window-status-dot,\s*\.session-agent-window-block > \.session-agent-row\.current \.agent-window-status-dot\s*\{[\s\S]*--subwindow-status-glyph-border-color:\s*var\(--subwindow-status-glyph-border-color-active\)/.test(paneTabsCss), 'active/current sub-window glyphs retain their visible border through the shared border token');
    assert.equal(/\.tmux-window-bar \.tmux-window-button\.active \.agent-window-status-dot[\s\S]{0,260}text-shadow:/.test(paneTabsCss), false, 'active tmux sub-window glyph contrast does not rely on text-shadow');
    assert.equal(/(?:\.pane-tab|\.dockview-pane-tab)[^{]*\.agent-window-status-dot::before/.test(paneTabsCss + sessionsCss), false, 'Dockview Tab status dots keep the original shared circle glyph and never get sub-window glyph pseudo-elements');
    assert.ok(/\.agent-window-status-dot\s*\{[\s\S]*font-family:\s*var\(--ui-font\)[\s\S]*font-stretch:\s*normal/.test(sessionsCss), 'agent status dots reset inherited condensed tab text so Tabber session-tab balls do not shrink');
    assert.ok(/\.session-agent-activity-marker \.agent-window-status-dot\s*\{[\s\S]*background:\s*var\(--agent-status-ball-fill, currentColor\)[\s\S]*border:\s*1px solid[\s\S]*filter:\s*none/.test(sessionsCss), 'aggregate Tab status balls are filled and bordered without a static halo');
    assert.ok(/agent-window-status-dot--segmented[\s\S]*agent-window-status-dot--\$\{aggregateTones\.join\('-'\)\}/.test(activitySource), 'the shared renderer marks mixed parent Tab balls with their complete tone identity');
    assert.ok(/agent-window-status-dot--attention-cooldown[\s\S]*conic-gradient\(var\(--bad\)[\s\S]*var\(--agent-status-cooldown\)/.test(sessionsCss), 'mixed red/yellow parent Tab balls use crisp conic segments instead of an averaged brown');
    assert.ok(/agent-window-status-dot--attention-working[\s\S]*conic-gradient\(var\(--bad\)[\s\S]*var\(--pr-status-passing\)/.test(sessionsCss), 'mixed red/green parent Tab balls use the shared two-tone fill');
    assert.ok(/agent-window-status-dot--attention-cooldown-working[\s\S]*conic-gradient\(var\(--bad\) 0 33\.333%[\s\S]*var\(--agent-status-cooldown\) 33\.333% 66\.666%[\s\S]*var\(--pr-status-passing\) 66\.666% 100%/.test(sessionsCss), 'mixed red/yellow/green parent Tab balls use three equal, crisp segments');
    assert.ok(/const AGENT_WINDOW_VISIBLE_TONES = Object\.freeze\(\[STATE_KEY\.working, 'attention', 'cooldown'\]\)[\s\S]*function agentWindowVisibleTone\(value\)[\s\S]*AGENT_WINDOW_VISIBLE_TONES\.includes\(value\)[\s\S]*function agentWindowStatusToneForItem\(item\)[\s\S]*item\?\.acknowledging === true\) return 'acknowledged';[\s\S]*item\?\.acknowledged === true\) return ''[\s\S]*agentWindowActivityTone\(item\.state\)[\s\S]*agentWindowVisibleTone\(tone\)/.test(activitySource), 'one shared status-tone classifier renders the temporary gray acknowledgement before removing an acknowledged window from every surface');
    assert.equal((activitySource.match(/AGENT_WINDOW_VISIBLE_TONES\.includes/g) || []).length, 1, 'every visible-tone membership check routes through the one shared helper');
    const coreSource = fs.readFileSync('static_src/js/yolomux/10_core_utils.js', 'utf8');
    assert.ok(/function acknowledgeTerminalAttentionFromUserAction\(session, windowIndex = null, options = \{\}\) \{[\s\S]*acknowledgeAgentWindowActivity\(sessionKey, resolvedWindowIndex[\s\S]*clearPromptAttentionForSession\(sessionKey/.test(coreSource), 'sub-window acknowledgement captures its gray visual before the shared prompt acknowledgement can hide the glyph');
    assert.ok(/const AGENT_WINDOW_AGGREGATE_TONES = Object\.freeze\(\['attention', 'cooldown', STATE_KEY\.working\]\)[\s\S]*function sessionAgentWindowStatusSummary\(session, info = null, autoPayload = null\)[\s\S]*visibleItems\.push\(\{agent, item, tone\}\)[\s\S]*const allAggregateTones = AGENT_WINDOW_AGGREGATE_TONES[\s\S]*pulseActive: visibleItems\.some[\s\S]*aggregateTones/.test(activitySource), 'the shared model retains every visible child tone while inheriting opacity pulse from any visible child');
    assert.ok(/function sessionStatusAgentWindowSummaryForTab\(session, info, payload = autoApproveStates\.get\(session\)\)[\s\S]*sessionAgentWindowStatusSummary\(session, info, payload\)/.test(popoverSource), 'the parent Tab delegates status classification to the shared model instead of maintaining a second classifier');
    assert.ok(/\.agent-window-activity--working \.agent-window-status-dot,[\s\S]*\.agent-window-activity--attention \.agent-window-status-dot,[\s\S]*\.agent-window-activity--cooldown \.agent-window-status-dot\s*\{[\s\S]*font-size:\s*var\(--agent-status-ball-size\)/.test(sessionsCss), 'agent status dots inherit glyph size from the shared activity wrapper');
    assert.equal(((sessionsCss + paneTabsCss).match(/--agent-status-ball-size:/g) || []).length, 2, 'agent status-ball size has only the base owner and shared sub-window 100% reference owner');
    assert.ok(/function agentWindowActivityIconHtml\(agentKey, state, idleSeconds, options = \{\}\)[\s\S]*const acknowledged = item\?\.acknowledged === true;[\s\S]*if \(acknowledged && statusOnly\) return ''[\s\S]*const markerHtml = acknowledged \? '' : agentWindowStatusDotHtml/.test(activitySource), 'acknowledgement removes the transient ball/play/pause/stop glyph while preserving the stable sub-window agent identity');
    assert.equal(/font-size:\s*calc\(var\(--agent-window-icon-size\)/.test(sessionsCss), false, 'status balls do not size themselves from the surface-specific agent icon token');
    assert.equal(/agent-symbol-status-alternate|agent-status-dot-alternate|--agent-alternate-animation-delay|--agent-alternate-pulse-duration/.test(sessionsCss + activitySource + layoutSource), false, 'agent status indicators no longer alternate symbol and ball');
    assert.equal(/\.agent-window-activity--attention,\s*\.agent-window-activity--cooldown\s*\{[\s\S]*display:\s*inline-grid/.test(sessionsCss), false, 'attention/cooldown agent glyphs and dots are not grid-stacked overlays');
    assert.ok(/agent-window-status-dot--transition-glow/.test(activitySource) && /\.agent-window-status-dot--transition-glow\.status-indicator--working,[\s\S]*\.agent-window-status-dot--transition-glow\.status-indicator--attention,[\s\S]*\.agent-window-status-dot--transition-glow\.status-indicator--cooldown\s*\{[\s\S]*box-shadow:\s*none[\s\S]*animation-name:\s*agent-status-opacity-pulse/.test(sessionsCss), 'fresh green/red/yellow transition dots use the same opacity pulse instead of a static glow');
    assert.ok(/function agentWindowTransitionPulseActive\(startedAt, nowSeconds = Date\.now\(\) \/ 1000\)[\s\S]*agentWindowTransitionGlowActive\(startedAt, nowSeconds\)/.test(activitySource) && /agent-window-status-dot--transition-pulse/.test(activitySource) && /\.agent-window-status-dot--transition-pulse:not\(\.heartbeat-pulse\)\s*\{[\s\S]*animation-name:\s*agent-status-opacity-pulse/.test(sessionsCss), 'new and color-changing status balls use the shared opacity pulse for the configured transition duration');
    assert.ok(/\.status-indicator--dot\.status-indicator--cooldown\.heartbeat-pulse\s*\{[\s\S]*animation-name:\s*agent-status-opacity-pulse/.test(sessionsCss), 'yellow cooldown status balls use the shared opacity pulse when pulsing is enabled');
    assert.ok(layoutSource.includes("status-indicator--cooldown', pulseEnabled ? 'heartbeat-pulse'") && layoutSource.includes("pulseEnabled ? 'attention-pulse'"), 'cooldown tone opts into the shared pulse classes only when status pulse is enabled');
    assert.ok(/\.status-indicator--cooldown\s*\{[^}]*color:\s*var\(--agent-status-cooldown\)[^}]*--agent-status-ball-fill:\s*var\(--agent-status-cooldown\)/.test(sessionsCss), 'cooldown markers use the vibrant agent yellow as their filled-ball owner');
    assert.equal(/\.status-indicator--dot\.status-indicator--working\.heartbeat-pulse,[\s\S]*?animation-name:\s*command-palette-thinking/.test(sessionsCss), false, 'status dots do not use the old command-palette-thinking pulse');
    assert.equal((sessionsCss + treeCss).includes('82 210 115'), false, 'working ring RGB literals do not escape the shared token sheet');
    assert.ok(/\.agent-window-agent-icon--active\.agent-icon\.codex\s*\{[^}]*--agent-working-glow-rgb:\s*102 126 248/.test(sessionsCss), 'the --active Codex glyph glows with the Codex icon color (working uses the green ball, not a per-agent glow)');
    assert.ok(/\.agent-window-agent-icon--active\.agent-icon\.claude\s*\{[^}]*--agent-working-glow-rgb:\s*207 117 84/.test(sessionsCss), 'the --active Claude glyph glows with the Claude icon color (working uses the green ball, not a per-agent glow)');
    assert.ok(/\.status-indicator--dot\.status-indicator--cooldown\s*\{[^}]*color:\s*var\(--agent-status-cooldown\)/.test(sessionsCss), 'cooldown dot is vibrant yellow');
    assert.ok(/\.status-indicator--active\s*\{[^}]*color:\s*var\(--file-tree-recency-max-contrast, var\(--text\)\)/.test(sessionsCss), 'active labels use the same max-contrast token as plain hot recency');
    assert.ok(/\.status-indicator--attention\s*\{[^}]*--agent-status-ball-fill:\s*var\(--bad\)[^}]*--attention-ring-rgb:\s*var\(--agent-status-attention-ring-rgb\)/.test(sessionsCss), 'attention markers use the shared red ring token instead of a local RGB tuple');
    assert.ok(/\.status-indicator--dot\.status-indicator--attention\s*\{[^}]*color:\s*var\(--bad\)/.test(sessionsCss), 'attention dot glyphs use saturated red instead of pale danger text');
    assert.equal(/status-indicator--idle[\s\S]{0,160}animation/.test(sessionsCss), false, 'idle circle markers stay static');
    assert.ok(/@media \(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*\.heartbeat-pulse[\s\S]*animation:\s*none/.test(sessionsCss), 'generic heartbeat motion is still suppressed by the reduced-motion rule before status indicators re-declare their pulse cadence');
    assert.ok(/\.attention-pulse\s*\{[^}]*animation-name:\s*attention-ring-fade/.test(sessionsCss), 'recency and attention share the attention-ring-fade animation parent');
    assert.ok(/@keyframes attention-ring-fade\s*\{[\s\S]*box-shadow:\s*0 0 0 0 rgb\(var\(--attention-ring-rgb, var\(--agent-status-attention-ring-rgb\)\) \/ 0\), 0 0 var\(--attention-ring-rest-glow-size, 5px\) rgb\(var\(--attention-ring-rgb, var\(--agent-status-attention-ring-rgb\)\) \/ var\(--attention-ring-rest-glow-alpha, 0\.24\)\)[\s\S]*box-shadow:\s*0 0 0 2px rgb\(var\(--attention-ring-rgb, var\(--agent-status-attention-ring-rgb\)\) \/ var\(--attention-ring-peak-outline-alpha, 0\.72\)\), 0 0 var\(--attention-ring-peak-glow-size, 26px\) rgb\(var\(--attention-ring-rgb, var\(--agent-status-attention-ring-rgb\)\) \/ var\(--attention-ring-peak-glow-alpha, 0\.68\)\)/.test(sessionsCss), 'attention-ring-fade uses the shared red token for every weak/strong fallback');
    assert.equal((sessionsCss + treeCss).includes('255 51 71'), false, 'attention ring RGB literals do not escape the shared token sheet');
    assert.equal((sessionsCss + treeCss).includes('245 197 66'), false, 'the stale cooldown RGB tuple is removed from every status surface');
    assert.ok(/@keyframes attention-ring-fade\s*\{[\s\S]*filter:\s*saturate\(var\(--attention-pulse-saturate-rest, 1\)\) brightness\(var\(--attention-pulse-brightness-rest, 1\)\)[\s\S]*filter:\s*saturate\(var\(--attention-pulse-saturate-peak, 1\)\) brightness\(var\(--attention-pulse-brightness-peak, 1\)\)/.test(sessionsCss), 'attention-ring-fade also carries the dot brightness pulse with neutral defaults for non-dot users');
    assert.ok(/\.attention-pulse\s*\{[^}]*animation-duration:\s*var\(--pulse-duration\)/.test(sessionsCss), 'shared attention pulse uses the shared pulse duration token');
    const syncRoot = api.testElementForId('body');
    const syncAgent = api.testElementForId('sync-agent-activity');
    syncAgent.className = 'agent-window-activity agent-window-activity--attention';
    syncAgent.style.setProperty('--attention-animation-delay', '-0.111s');
    const syncDot = api.testElementForId('sync-agent-dot');
    syncDot.className = 'status-indicator status-indicator--dot agent-window-status-dot status-indicator--attention heartbeat-pulse attention-pulse';
    syncDot.style.setProperty('--attention-animation-delay', '-0.222s');
    let syncDotCurrentTime = -1;
    const syncDotAnimation = {
      animationName: 'attention-ring-fade',
      effect: {getTiming: () => ({duration: 1550})},
      cancel() { this.cancelled = (this.cancelled || 0) + 1; },
      play() { this.played = (this.played || 0) + 1; },
      set currentTime(value) { syncDotCurrentTime = value; },
      get currentTime() { return syncDotCurrentTime; },
    };
    syncDot.getAnimations = () => [syncDotAnimation];
    syncAgent.appendChild(syncDot);
    const syncAttentionLabel = api.testElementForId('sync-attention-label');
    syncAttentionLabel.className = 'status-indicator status-indicator--label status-indicator--attention heartbeat-pulse attention-pulse';
    syncAttentionLabel.style.setProperty('--attention-animation-delay', '-0.999s');
    let syncAttentionLabelCurrentTime = -2;
    const syncAttentionLabelAnimation = {
      animationName: 'attention-ring-fade',
      effect: {getTiming: () => ({duration: 1550})},
      cancel() { this.cancelled = (this.cancelled || 0) + 1; },
      play() { this.played = (this.played || 0) + 1; },
      set currentTime(value) { syncAttentionLabelCurrentTime = value; },
      get currentTime() { return syncAttentionLabelCurrentTime; },
    };
    syncAttentionLabel.getAnimations = () => [syncAttentionLabelAnimation];
    syncRoot.appendChild(syncAgent);
    syncRoot.appendChild(syncAttentionLabel);
    api.syncAgentWindowActivityAnimationDelaysForTest(syncRoot);
    const syncedDelay = api.documentElementStyleForTest().getPropertyValue('--attention-animation-delay');
    assert.notEqual(syncedDelay, '', 'attention indicators inherit one root animation delay');
    assert.equal(syncAgent.style.getPropertyValue('--attention-animation-delay'), '', 'agent activity wrapper stale local animation delay is cleared');
    assert.equal(syncDot.style.getPropertyValue('--attention-animation-delay'), '', 'red agent status dot stale local animation delay is cleared');
    assert.equal(syncAttentionLabel.style.getPropertyValue('--attention-animation-delay'), '', 'attention label stale local animation delay is cleared');
    assert.equal(syncDotCurrentTime, syncAttentionLabelCurrentTime, 'red agent status dot and attention label animation currentTime are forced to the same sampled phase');
    assert.ok(syncAttentionLabelCurrentTime > 0, 'sampled attention animation timeline is a positive shared clock value');
    const firstSyncedDelay = api.documentElementStyleForTest().getPropertyValue('--attention-animation-delay');
    api.syncAgentWindowActivityAnimationDelaysForTest(syncRoot);
    assert.equal(api.documentElementStyleForTest().getPropertyValue('--attention-animation-delay'), firstSyncedDelay, 'a second sync keeps the same root animation delay instead of restarting the CSS animation');
    api.restartAgentWindowActivityPulseAnimationsForTest(syncRoot);
    assert.equal(syncDotAnimation.cancelled, 1, 'a pulse-period change restarts an existing status-dot animation without a DOM mutation');
    assert.equal(syncDotAnimation.played, 1, 'the restarted status dot uses the new CSS duration');
    assert.equal(syncAttentionLabelAnimation.cancelled, 1, 'the shared owner also restarts matching attention-label pulses');
    assert.equal(syncAttentionLabelAnimation.played, 1, 'the shared owner resumes every matching active pulse');
    assert.ok(/let agentStatusPulsePeriodMs = initialSetting\('performance\.agent_status_pulse_period_ms'\)/.test(bootstrapSource), 'status ball pulse period initializes from the persisted setting');
    assert.ok(/agentStatusPulsePeriodMs = numberSetting\('performance\.agent_status_pulse_period_ms'\)/.test(settingsRuntimeSource), 'status ball pulse period live-updates from settings changes');
    assert.ok(/const statusPulsePeriodMs = Math\.max\(1, agentStatusPulsePeriodMs\)/.test(settingsRuntimeSource) && /root\.setProperty\('--pulse-duration', `\$\{statusPulsePeriodMs \/ 1000\}s`\)/.test(settingsRuntimeSource), 'status balls use the shared setting-backed transition pulse cadence');
    assert.ok(/root\.setProperty\('--status-pulse-step-count', String\(Math\.max\(1, Math\.round\(statusPulsePeriodMs \/ 125\)\)\)\)/.test(settingsRuntimeSource), 'status ball transition pulse uses one discrete step per roughly 125ms');
    assert.ok(/const previousAgentStatusPulsePeriodMs = agentStatusPulsePeriodMs;[\s\S]*agentStatusPulsePeriodMs = numberSetting\('performance\.agent_status_pulse_period_ms'\)[\s\S]*previousAgentStatusPulsePeriodMs !== agentStatusPulsePeriodMs[\s\S]*restartAgentWindowActivityPulseAnimations\(\)/.test(settingsRuntimeSource), 'a runtime pulse-period update restarts existing status animations through the shared synchronization owner');
    assert.ok(/--pulse-duration:\s*1\.55s/.test(tokensCss), 'status pulse duration fallback matches the 1550ms default');
    assert.ok(/--status-pulse-step-count:\s*12/.test(tokensCss) && /--status-pulse-timing:\s*steps\(var\(--status-pulse-step-count\),\s*end\)/.test(tokensCss), 'status pulse timing defaults to twelve roughly-125ms visual steps per 1550ms period');
    assert.ok(/\.agent-window-status-dot--transition-pulse:not\(\.heartbeat-pulse\)\s*\{[\s\S]*animation-timing-function:\s*var\(--status-pulse-timing\)/.test(sessionsCss), 'transition status balls use the stepped timing token');
    assert.ok(/\.attention-pulse\s*\{[^}]*animation-timing-function:\s*var\(--pulse-easing\)/.test(sessionsCss), 'shared attention pulse uses the shared pulse easing token');
    assert.ok(/\.ci-indicator\.metadata-pulse:not\(\.pr-status-failing\)\s*\{[^}]*animation-name:\s*metadata-badge-pulse;[^}]*animation-duration:\s*var\(--pulse-duration\);[^}]*animation-timing-function:\s*var\(--pulse-easing\);[^}]*animation-iteration-count:\s*infinite;/.test(sessionsCss), 'metadata pulse repeats until the server-window class is removed');
    assert.equal(/metadata-badge-pulse var\(--pulse-duration\) var\(--pulse-easing\) 14/.test(sessionsCss), false, 'metadata pulse no longer has a fixed iteration count');
    assert.equal(/900ms ease-in-out infinite alternate|metadata-badge-pulse 1\.4s/.test(sessionsCss), false, 'old hardcoded pulse durations are gone from session/popover CSS');
    assert.ok(/\.file-tree-date\s*\{[\s\S]*border:\s*1px solid transparent[\s\S]*border-radius:\s*5px/.test(treeCss), 'recency date cells have a visible border target for the shared attention-ring animation');
    assert.ok(/\.file-explorer-changes-panel \.file-tree-date\s*\{[^}]*font-size:\s*70%/.test(changesCss), 'Differ relative timestamps are 70% of the filename font size');
    assert.equal(/file-tree-recency-pulse/.test(treeCss + activitySource), false, 'the old standalone file-tree recency pulse is gone');
    assert.equal(/10s ease-out/.test(treeCss), false, 'recency no longer uses the old one-shot ten-second pulse');
    assert.ok((tokensCss.match(/--file-tree-recency-hot:\s*var\(--file-tree-recency-max-contrast\);/g) || []).length >= 2, 'plain hot recency uses the shared max-contrast token in dark and light themes');
    assert.equal(/--file-tree-recency-hot:\s*var\(--bad\)/.test(tokensCss), false, 'plain hot recency is not red; red is attention-only');
    assert.ok(/\.file-tree-row:not\(\.selected\):not\(\.current-file\)\.file-tree-recency-just-updated > \.file-tree-date,[\s\S]*?\.file-tree-recency-hot > \.file-tree-date,[\s\S]*?\.file-tree-recency-fresh > \.file-tree-date\s*\{[\s\S]*font-weight:\s*800/.test(treeCss), 'newest recency rows stay bold through the shared date-cell rule');

    api.setFileTreeRecencyNowForTest(nowMs);
    assert.equal(api.fileTreeRecencyStateForMtimeForTest(nowSeconds - 5, nowMs).key, 'just-updated', 'very recent mtime maps to the pulse-eligible recency bucket');
    assert.equal(api.fileTreeRecencyStateForMtimeForTest(nowSeconds - 30, nowMs).key, 'hot', 'sub-minute mtime maps to the brightest non-pulsing recency bucket');
    assert.equal(api.fileTreeRecencyStateForMtimeForTest(nowSeconds - 4 * 60, nowMs).key, 'fresh', 'five-minute-window mtime maps to the fresh recency bucket');
    assert.equal(api.fileTreeRecencyStateForMtimeForTest(nowSeconds - 9 * 60, nowMs).key, 'recent', 'sub-ten-minute mtime still gets a recent recency bucket');
    assert.equal(api.fileTreeRecencyStateForMtimeForTest(nowSeconds - 50 * 60, nowMs).key, 'recent', 'hour-window mtime maps to a middle recency bucket');
    assert.equal(api.fileTreeRecencyStateForMtimeForTest(nowSeconds - 3 * 24 * 60 * 60, nowMs).key, 'old', 'old mtime maps to the gray bucket');

    api.setFileExplorerTreeDateModeForTest('relative');
    const tree = new TestElement('finder-recency-tree');
    tree.setAttribute('role', 'tree');
    tree.classList.add('file-explorer-tree-panel');
    api.renderTreeChildrenForTest(tree, '/repo', entries);
    const rows = rowMap(tree);
    assert.equal(rows['/repo/just.md'].dataset.recency, 'just-updated', 'Ago mode marks very recent Finder rows just-updated');
    assert.equal(rows['/repo/just.md'].classList.contains('file-tree-recency-just-updated'), true, 'Ago mode applies the just-updated row class');
    assert.equal(dateCell(rows['/repo/just.md']).textContent, '<15 sec ago', 'Ago mode labels the pulse window with the matching sub-15-second text');
    assert.equal(dateCell(rows['/repo/just.md']).classList.contains('attention-pulse'), true, 'very recent Finder rows pulse their date cell with the shared attention class');
    assert.equal(dateCell(rows['/repo/just.md']).classList.contains('heartbeat-pulse'), true, 'very recent Finder rows inherit the shared heartbeat timing parent');
    assert.notEqual(dateCell(rows['/repo/just.md']).style.getPropertyValue('--attention-animation-delay'), '', 'date-cell pulse is phase-aligned with attentionAnimationDelay');
    assert.equal(rows['/repo/just.md'].style.getPropertyValue('--file-tree-recency-date-color'), 'var(--file-tree-recency-hot)', 'just-updated rows expose the token-backed date color');
    assert.equal(rows['/repo/hot.md'].dataset.recency, 'hot', 'Ago mode marks sub-minute Finder rows hot after the pulse window');
    assert.equal(dateCell(rows['/repo/hot.md']).textContent, '<1 min ago', 'Ago mode labels 15-60s rows as under one minute without pulsing');
    assert.equal(rows['/repo/hot.md'].style.getPropertyValue('--file-tree-recency-date-color'), 'var(--file-tree-recency-hot)', 'hot rows expose the same max-contrast date color');
    assert.equal(dateCell(rows['/repo/hot.md']).classList.contains('attention-pulse'), false, 'hot-but-not-just-updated rows do not pulse');
    assert.equal(rows['/repo/fresh.md'].dataset.recency, 'fresh', 'Ago mode marks fresh Finder rows without pulsing');
    assert.equal(rows['/repo/fresh.md'].style.getPropertyValue('--file-tree-recency-date-color'), 'var(--file-tree-recency-fresh)', 'older fresh rows keep their existing graduated color token');
    assert.equal(dateCell(rows['/repo/fresh.md']).classList.contains('attention-pulse'), false, 'fresh recency rows do not pulse');
    assert.equal(rows['/repo/ten.md'].dataset.recency, 'recent', 'Ago mode marks sub-ten-minute Finder rows recent without pulsing');
    assert.equal(rows['/repo/hour.md'].dataset.recency, 'recent', 'Ago mode marks hour-window Finder rows without pulsing');
    assert.equal(dateCell(rows['/repo/hour.md']).classList.contains('attention-pulse'), false, 'hour-window recency rows do not pulse');
    assert.equal(rows['/repo/old.md'].dataset.recency, 'old', 'Ago mode keeps old Finder rows in the gray bucket');
    assert.equal(dateCell(rows['/repo/old.md']).classList.contains('attention-pulse'), false, 'old Finder rows never pulse');

    api.setFileExplorerSelectionForTest(['/repo/just.md']);
    api.renderTreeChildrenForTest(tree, '/repo', entries);
    assert.equal(rows['/repo/just.md'].dataset.recency, 'just-updated', 'selected rows still track the recency tier');
    assert.equal(dateCell(rows['/repo/just.md']).classList.contains('attention-pulse'), false, 'selected rows suppress the recency attention pulse so selection colors win');
    api.setFileExplorerSelectionForTest([]);
    api.renderTreeChildrenForTest(tree, '/repo', entries);
    assert.equal(dateCell(rows['/repo/just.md']).classList.contains('attention-pulse'), true, 'clearing selection restores the pulse while the mtime is still fresh');

    const firstPulseUntil = rows['/repo/just.md'].__fileTreeRecencyAttentionUntilMs;
    api.renderTreeChildrenForTest(tree, '/repo', entries);
    assert.equal(rows['/repo/just.md'].__fileTreeRecencyAttentionUntilMs, firstPulseUntil, 'same-mtime Finder refresh keeps the same stop time');
    api.setFileTreeRecencyNowForTest(nowMs + 10001);
    api.renderTreeChildrenForTest(tree, '/repo', entries);
    assert.equal(rows['/repo/just.md'].dataset.recency, 'hot', 'rows settle into the hot tier after the fifteen-second pulse window');
    assert.equal(dateCell(rows['/repo/just.md']).textContent, '<1 min ago', 'the label switches with the same fifteen-second boundary as the pulse');
    assert.equal(dateCell(rows['/repo/just.md']).classList.contains('attention-pulse'), false, 'pulse class stops after the fifteen-second mtime window');

    const updatedEntries = entries.map(entry => entry.name === 'just.md'
      ? {...entry, mtime: (nowMs + 10001) / 1000 - 5}
      : entry);
    api.renderTreeChildrenForTest(tree, '/repo', updatedEntries);
    assert.equal(rows['/repo/just.md'].dataset.recency, 'just-updated', 'mtime changes put the row back in the just-updated tier');
    assert.equal(dateCell(rows['/repo/just.md']).classList.contains('attention-pulse'), true, 'mtime changes restart the shared date-cell pulse');
    assert.ok(rows['/repo/just.md'].__fileTreeRecencyAttentionUntilMs > firstPulseUntil, 'mtime-change pulse gets a new stop time');

    api.setFileExplorerTreeDateModeForTest('date');
    api.renderTreeChildrenForTest(tree, '/repo', updatedEntries);
    assert.equal(rows['/repo/just.md'].dataset.recency, 'just-updated', 'Date mode preserves Finder recency data');
    assert.equal(rows['/repo/just.md'].classList.contains('file-tree-recency-just-updated'), true, 'Date mode preserves Finder recency classes');
    assert.equal(dateCell(rows['/repo/just.md']).classList.contains('attention-pulse'), true, 'Date mode keeps the shared pulse on the date cell');
    assert.equal(rows['/repo/ten.md'].dataset.recency, 'recent', 'Date mode preserves sub-ten-minute Finder recency');

    api.setFileExplorerTreeDateModeForTest('none');
    api.renderTreeChildrenForTest(tree, '/repo', updatedEntries);
    assert.equal(rows['/repo/just.md'].dataset.recency, undefined, 'None mode also leaves Finder recency data unset');
    assert.equal(dateCell(rows['/repo/just.md']).classList.contains('attention-pulse'), false, 'None mode removes date-cell attention pulse');

    api.setFileExplorerTreeDateModeForTest('relative');
    const differTree = new TestElement('differ-recency-tree');
    differTree.setAttribute('role', 'tree');
    differTree.classList.add('file-explorer-tree-panel');
    api.renderTreeChildrenForTest(differTree, '/repo', updatedEntries, 0, [], {differMode: true});
    const differRows = rowMap(differTree);
    assert.equal(differRows['/repo/just.md'].dataset.recency, 'just-updated', 'Differ Ago rows use the shared recency state');
    assert.equal(dateCell(differRows['/repo/just.md']).classList.contains('attention-pulse'), true, 'Differ very recent rows pulse from shared recency rules');
    assert.equal(differRows['/repo/ten.md'].dataset.recency, 'recent', 'Differ Ago rows keep graduated recent styling');
    api.setFileExplorerTreeDateModeForTest('date');
    api.renderTreeChildrenForTest(differTree, '/repo', updatedEntries, 0, [], {differMode: true});
    assert.equal(differRows['/repo/just.md'].dataset.recency, 'just-updated', 'Differ Date rows preserve the recency signal');
    assert.equal(dateCell(differRows['/repo/just.md']).classList.contains('attention-pulse'), true, 'Differ Date rows keep the shared pulse');
    api.setFileTreeRecencyNowForTest(null);
  });

  test('t@6215', () => {
    const api = loadYolomux('', ['1']);
    const path = '/repo/app/common.py';
    const normalRows = api.filePopoverRows(path, {kind: 'text', size: 42}).join('');
    assert.equal((normalRows.match(/popover-copy-value/g) || []).length, 1);
    assert.ok(normalRows.includes('data-copy-path="/repo/app/common.py"'), 'file popover path copy uses the shared delegated copy attr');
    assert.equal(normalRows.includes('data-copy-popover-path'), false, 'file popover path copy no longer emits the dead popover-only copy attr');
    assert.equal(normalRows.includes('popover-subtitle'), false);
    assert.ok(normalRows.includes('file editor'));
    assert.equal(normalRows.includes('status'), false);
    const dirtyRows = api.filePopoverRows(path, {kind: 'text', dirty: true}).join('');
    assert.ok(dirtyRows.includes('status'));
    assert.ok(dirtyRows.includes('modified'));
  });

  test('t@6228', () => {
    const api = loadYolomux('', ['1']);
    const signature = api.directoryEntriesSignature([
      {name: 'b.txt', kind: 'file', size: 2, mtime: 20},
      {name: 'a.txt', kind: 'file', size: 1, mtime: 10},
    ]);
    assert.equal(signature, api.directoryEntriesSignature([
      {name: 'a.txt', kind: 'file', size: 1, mtime: 10},
      {name: 'b.txt', kind: 'file', size: 2, mtime: 20},
    ]));
    assert.notEqual(signature, api.directoryEntriesSignature([
      {name: 'a.txt', kind: 'file', size: 1, mtime: 11},
      {name: 'b.txt', kind: 'file', size: 2, mtime: 20},
    ]));
    assert.equal(api.fileEntryChanged({mtime: 10, size: 1}, {mtime: 10, size: 1}), false);
    assert.equal(api.fileEntryChanged({mtime: 10, size: 1}, {mtime: 11, size: 1}), true);
    assert.equal(api.fileEntryChanged({mtime: 1780806618930051800, size: 1}, {mtime_ns: 1780806618930051885, size: 1}), false);
    assert.equal(api.fileEntryChanged({mtime: 1780806618930051800, size: 1}, {mtime_ns: 1780806618950051800, size: 1}), true);
    assert.equal(api.fileEntryChanged({mtime: 10, size: 1}, {mtime: 10, size: 2}), true);
  });

  test('t@6249', () => {
    const api = loadYolomux('', ['1']);
    const imagePath = '/home/test/a.png';
    const viewerItem = api.registerImageViewerLayoutItem(imagePath);
    assert.equal(viewerItem, api.imageViewerItemFor(imagePath));
    assert.deepStrictEqual(canonical(api.openFileEditorItems()), [viewerItem]);
    assert.deepStrictEqual(canonical(api.filePanelItemsForPath(imagePath)), [viewerItem]);
    assert.equal(api.fileItemPath(viewerItem), imagePath);
    const fileItem = api.registerFileEditorLayoutItem(imagePath);
    assert.equal(fileItem, api.fileEditorItemFor(imagePath));
    assert.deepStrictEqual(canonical(api.openFileEditorItems()), [viewerItem, fileItem]);
    assert.deepStrictEqual(canonical(api.filePanelItemsForPath(imagePath)), [viewerItem, fileItem]);
    assert.equal(api.imageOpenUsesSharedViewer(), true);
    assert.equal(api.imageOpenUsesSharedViewer({forceNewTab: true}), false);
    assert.equal(api.imageOpenUsesSharedViewer({targetSlot: 'left'}), false);

    api.setOpenFileStateForTest(imagePath, {kind: 'error', dirty: false, externalMissing: true, error: 'file deleted or moved on disk'});
    assert.equal(api.openFileIsMissing(imagePath), true);
    const missingHtml = api.fileEditorPaneTabHtml(fileItem);
    assert.ok(missingHtml.includes('file-tab-missing-badge'), 'missing file tabs show a badge');
    assert.ok(missingHtml.includes('a.png'), 'missing file tabs still show the basename');
    assert.equal(api.openFileStatus({kind: 'text', externalError: 'network down'}).message.includes('file state unknown'), true);
    assert.equal(api.openFileStatus({kind: 'text', externalError: 'network down'}).message.includes('deleted'), false, 'network/list refresh errors are not reported as deletion');
    assert.equal(
      api.fileInspectionErrorMessageForTest({payload: {error: 'outside allowed root'}, status: 403}, '/home/test/yolomux.dev3/docs/preview-samples/03-mixed.md'),
      'outside allowed root (HTTP 403)',
      'file inspection preserves the backend reason and HTTP status before falling back to the generic path message',
    );
    assert.equal(api.openFileStatus({kind: 'text', externalError: 'outside allowed root (HTTP 403)'}).message.includes('outside allowed root (HTTP 403)'), true);
  });

  test('t@6274', () => {
    const api = loadYolomux('', ['1']);
    const state = api.fileContextMenuState({kind: 'file'}, ['/repo/app/a.txt'], ['a.txt']);
    assert.equal(state.copyRelativeDisabled, false);
    assert.equal(state.openInNewTabDisabled, false, 'text files can open a second editor tab from the shared file context menu');
    assert.equal(state.downloadDisabled, false);
    assert.equal(state.zipDownloadDisabled, true);
    assert.equal(state.renameDisabled, false);
    assert.equal(state.deleteDisabled, false);
    const imageState = api.fileContextMenuState({kind: 'file', name: 'screen.png'}, ['/repo/app/screen.png'], ['screen.png']);
    assert.equal(imageState.openInNewTabDisabled, false);
    const dirState = api.fileContextMenuState({kind: 'dir'}, ['/repo/app'], ['']);
    assert.equal(dirState.downloadDisabled, true, 'folders keep the existing plain Download disabled');
    assert.equal(dirState.zipDownloadDisabled, false, 'single folder rows can zip and download');
    const multiDirState = api.fileContextMenuState({kind: 'dir'}, ['/repo/app', '/repo/other'], ['', '']);
    assert.equal(multiDirState.zipDownloadDisabled, true, 'multi-select folders do not offer one ambiguous zip download');

    const readonlyApi = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'readonly');
    const readonlyState = readonlyApi.fileContextMenuState({kind: 'file'}, ['/repo/app/a.txt'], ['a.txt']);
    // readonly is terminal-only — the server 403s every /api/fs/* read, so Download and file tab opens
    // are disabled in readonly to match, rather than offering a command that fails.
    assert.equal(readonlyState.downloadDisabled, true, 'readonly cannot download (server forbids /api/fs/raw)');
    const readonlyImage = readonlyApi.fileContextMenuState({kind: 'file', name: 'screen.png'}, ['/repo/app/screen.png'], ['screen.png']);
    assert.equal(readonlyImage.openInNewTabDisabled, true, 'readonly cannot open a file in a tab (server forbids the read)');
    const readonlyDir = readonlyApi.fileContextMenuState({kind: 'dir'}, ['/repo/app'], ['']);
    assert.equal(readonlyDir.zipDownloadDisabled, true, 'readonly cannot zip a folder (server forbids filesystem downloads)');
    assert.equal(readonlyState.renameDisabled, true);
    assert.equal(readonlyState.deleteDisabled, true);
    const actionSource = fs.readFileSync('static_src/js/yolomux/45_file_explorer_actions.js', 'utf8');
    assert.ok(actionSource.includes("if (entry?.kind === 'dir')") && actionSource.includes("'Zip & download'"), 'Zip & download is appended only from the folder branch of the shared Finder context menu');
  });

  test('t@6296', () => {
    const api = loadYolomux('', ['1']);
    const html = api.transcriptPathRowHtml('/tmp/yolomux/session.jsonl');
    assert.ok(html.includes('/tmp/yolomux/session.jsonl'));
    assert.ok(html.includes('data-copy-path'));
    assert.equal(api.transcriptPathRowHtml('').includes('no transcript path'), true);
  });

  test('path copy buttons route through one delegated handler', () => {
    const jsFiles = fs.readdirSync('static_src/js/yolomux')
      .filter(file => file.endsWith('.js'))
      .sort()
      .map(file => `static_src/js/yolomux/${file}`);
    const handlerSites = [];
    let source = '';
    for (const file of jsFiles) {
      const text = fs.readFileSync(file, 'utf8');
      source += text;
      for (const match of text.matchAll(/delegate\([^;\n]*'\[data-copy-path\]'[^;\n]*\)|closest\('\[data-copy-path\]'\)/g)) {
        handlerSites.push(`${file}:${match[0]}`);
      }
    }
    assert.deepStrictEqual(handlerSites, [
      "static_src/js/yolomux/10_core_utils.js:delegate(document, 'pointerup', '[data-copy-path]', handleCopyPathPointerUp, {capture: true})",
      "static_src/js/yolomux/10_core_utils.js:delegate(document, 'click', '[data-copy-path]', handleCopyPathClick, {capture: true})",
    ], 'all data-copy-path clicks are handled by the shared delegated owner');
    assert.ok(source.includes('globalThis.isSecureContext !== false && clipboard?.writeText'), 'copy avoids the async clipboard API when the page is explicitly insecure');
    assert.ok(source.includes('if (copyTextToClipboardViaCopyEvent(value)) return;'), 'copy falls back through a synchronous copy event before the textarea fallback');
    assert.ok(source.includes('const OFFSCREEN_POSITION_PX = -10000;'), 'off-screen JS positioning uses one named constant');
    assert.ok(/textarea\.style\.left = `\$\{OFFSCREEN_POSITION_PX\}px`;[\s\S]*textarea\.style\.top = `\$\{OFFSCREEN_POSITION_PX\}px`;/.test(source), 'textarea clipboard fallback routes both off-screen axes through the shared constant');
    assert.ok(source.includes("statusOk(localizedHtml('status.copied'))"), 'copy success reports a generic copied status for path, session ID, and transcript buttons');
    assert.ok(source.includes("statusErr(localizedHtml('status.copyFailed', {error}))"), 'copy failure reports the error through the shared status line');
    assert.equal(source.includes('data-copy-transcript-path'), false, 'terminal transcript path no longer uses a parallel copy attribute');
  });

  await testAsync('popover copy buttons copy on pointerup/click and leave the popover open', async () => {
    const api = loadYolomux('', ['1']);
    const popover = new TestElement('copy-popover');
    popover.className = 'session-popover popover-open';
    const button = new TestElement('copy-button', 'button');
    button.dataset.copyPath = '/repo/app/common.py';
    popover.appendChild(button);
    const dispatch = (type, target, detail = 1) => {
      const event = {
        type,
        detail,
        target,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.propagationStopped = true; },
        stopImmediatePropagation() { this.immediatePropagationStopped = true; },
      };
      for (const listener of api.documentListenersForTest(type)) listener(event);
      return event;
    };

    api.clearClipboardTextForTest();
    const pointerEvent = dispatch('pointerup', button, 1);
    await flushAsyncWork();
    assert.equal(api.clipboardTextForTest(), '/repo/app/common.py', 'pointerup copies the full value before the popover stops bubble-phase clicks');
    assert.equal(pointerEvent.defaultPrevented, true, 'copy pointerup suppresses tab/popover activation');
    assert.equal(pointerEvent.propagationStopped, true, 'copy pointerup does not bubble into popover dismissal');
    assert.ok(api.statusHtmlForTest().includes('copied'), 'copy success gives visible feedback');
    assert.equal(popover.classList.contains('popover-open'), true, 'copy success leaves the popover open');

    button.dataset.copyPath = '/repo/app/duplicate.py';
    dispatch('click', button, 1);
    await flushAsyncWork();
    assert.equal(api.clipboardTextForTest(), '/repo/app/common.py', 'the pointer-generated click is ignored after pointerup copies once');

    button.dataset.copyPath = 'keyboard-session-id';
    dispatch('click', button, 0);
    await flushAsyncWork();
    assert.equal(api.clipboardTextForTest(), 'keyboard-session-id', 'keyboard click activation still copies');
    assert.equal(popover.classList.contains('popover-open'), true, 'keyboard copy also leaves the popover open');
  });

  test('t@6304', () => {
    const api = loadYolomux('', ['1']);
    assert.equal(api.editorWrapValue(false), 'off');
    assert.equal(api.editorWrapValue(true), 'soft');
    assert.equal(api.rawFileUrl('/repo/app/a b.txt', {v: 7}), '/api/fs/raw?path=%2Frepo%2Fapp%2Fa%20b.txt&v=7');
    assert.equal(api.rawFileUrl('/repo/app/image.png', {v: api.fileEditorImageVersionForTest({mtime: 7, mtime_ns: 7000000001, size: 1234})}), '/api/fs/raw?path=%2Frepo%2Fapp%2Fimage.png&v=7000000001');
    assert.equal(api.fileEditorImageVersionForTest({mtime: 7, size: 1234}), '7');
    assert.equal(api.rawFileDownloadUrl('/repo/app/a b.txt'), '/api/fs/raw?path=%2Frepo%2Fapp%2Fa%20b.txt&download=1');
    assert.equal(api.zipFileDownloadUrl('/repo/app/a b'), '/api/fs/zip?path=%2Frepo%2Fapp%2Fa%20b');
    assert.equal(api.downloadFilenameFromContentDisposition('attachment; filename="calvin.20261225-120001.zip"', 'fallback.zip'), 'calvin.20261225-120001.zip');
    assert.equal(api.downloadFilenameFromContentDisposition("attachment; filename*=UTF-8''calvin%20space.zip", 'fallback.zip'), 'calvin space.zip');
    assert.deepStrictEqual({...api.markdownPreviewImageTarget('.uploads/pasted image.png', '/repo/docs/note.md')}, {
      src: '/api/fs/raw?path=%2Frepo%2Fdocs%2F.uploads%2Fpasted%20image.png',
      path: '/repo/docs/.uploads/pasted image.png',
      external: false,
    }, 'Markdown preview resolves editor-pasted relative .uploads images beside the Markdown file');
  });

  test('t@6312', () => {
    const api = loadYolomux('', ['1']);
    const pixelWheel = api.terminalWheelSignedLines({deltaY: 105, deltaMode: 0}, 40);
    assert.ok(pixelWheel > 2.5 && pixelWheel < 3.5, 'mouse-like pixel wheel remains about three lines');
    const touchpadTick = api.terminalWheelSignedLines({deltaY: 4, deltaMode: 0}, 40);
    assert.ok(touchpadTick > 0 && touchpadTick < 0.2, 'small touchpad pixel deltas accumulate as fractions');
    assert.equal(api.terminalWheelSignedLines({deltaY: -3, deltaMode: 1}, 40), -3);
    assert.equal(api.terminalWheelSignedLines({deltaY: 1, deltaMode: 2}, 40), 12);
    assert.equal(api.terminalWheelSignedLines({deltaY: 999, deltaMode: 0}, 40), 12);
    assert.equal(api.terminalWheelSignedLines({deltaY: 4, deltaMode: 0, ctrlKey: true}, 40), 0);
    assert.equal(api.terminalWheelSignedLines({deltaY: 4, deltaMode: 0, shiftKey: true}, 40), 34);
  });

  test('t@6318', () => {
    // Regression: alt-screen panes (claude/codex/vim) must NOT route the wheel into tmux copy-mode.
    // Their tmux pane has no scrollback, so the wheel has to reach the app instead. The wheel handler
    // gates on sessionPaneIsAlternateScreen.
    const api = loadYolomux('', ['1']);
    const altScreenPane = {
      windows: [{
        key: '1:0', session: '1', window_index: '0', active: true,
        panes: [{
          window_key: '1:0', session: '1', window_index: '0', pane_index: '0',
          target: '%11', pane_id: '%11', current_command: 'claude',
          active: true, alternate_on: true, pid: 1234, dead: false,
        }],
      }],
    };
    api.setTmuxSignalStateForTest(altScreenPane);
    assert.equal(api.sessionPaneIsAlternateScreen('1'), true, 'claude alt-screen pane defers the wheel to the app');
    const shellPane = JSON.parse(JSON.stringify(altScreenPane));
    shellPane.windows[0].panes[0].current_command = 'bash';
    shellPane.windows[0].panes[0].alternate_on = false;
    api.setTmuxSignalStateForTest(shellPane);
    assert.equal(api.sessionPaneIsAlternateScreen('1'), false, 'a normal shell pane keeps tmux copy-mode scrollback');
    api.setTmuxSignalStateForTest(null);
    assert.equal(api.sessionPaneIsAlternateScreen('1'), false, 'no signal state means no alt-screen claim');
  });

  test('t@6325', () => {
    const api = loadYolomux('', ['1']);
    assert.equal(api.agentErrorIsBlocking('codex transcript not found by process fd or cwd'), false);
    assert.equal(api.agentErrorIsBlocking('missing /home/test/.claude/sessions/123.json'), false);
    assert.equal(api.agentErrorIsBlocking('worker crashed'), true);
    assert.notEqual(api.sessionState('1', {agents: [{kind: 'codex', error: 'codex transcript not found by process fd or cwd'}]}).key, 'blocked');
    assert.notEqual(api.sessionState('1', {agents: [{kind: 'claude', error: 'missing /home/test/.claude/sessions/123.json'}]}).key, 'blocked');
    assert.equal(api.sessionState('1', {agents: [{kind: 'codex', error: 'worker crashed'}]}).key, 'blocked');
  });

  test('t@6335', () => {
    const api = loadYolomux('', ['1']);
    api.setAutoApproveStateForTest('1', {
      enabled: false,
      prompt: {visible: false},
      screen: {key: 'approval', text: 'Do you want to proceed?'},
    });
    const state = api.sessionState('1', {agents: [{kind: 'codex'}], panes: []});
    assert.equal(state.key, 'needs-approval', 'roster screen approval state lights attention even when prompt.visible is absent');
    assert.equal(state.reason, 'Do you want to proceed?');
  });

  test('t@6341', () => {
    const api = loadYolomux('', ['1']);
    api.setDocumentTitleNowForTest(200000);
    api.setTmuxSignalStateForTest({
      windows: [{
        key: '1:0',
        session: '1',
        window_index: '0',
        activity_ts: 199,
        bell_flag: false,
        silence_flag: false,
        panes: [{
          window_key: '1:0',
          session: '1',
          window_index: '0',
          pane_index: '0',
          target: '%11',
          pane_id: '%11',
          current_command: 'codex',
          alternate_on: true,
          pid: 1234,
          dead: false,
        }],
      }],
    });
    assert.equal(api.sessionState('1', {agents: [], panes: []}).key, 'working', 'tmux command + pid/alternate screen marks an agent running');
    api.setTmuxSignalStateForTest({
      windows: [{
        key: '1:0',
        session: '1',
        window_index: '0',
        activity_ts: 10,
        bell_flag: false,
        silence_flag: true,
        panes: [{
          window_key: '1:0',
          session: '1',
          window_index: '0',
          pane_index: '0',
          target: '%11',
          pane_id: '%11',
          current_command: 'codex',
          alternate_on: true,
          pid: 1234,
          dead: false,
        }],
      }],
    });
    const silent = api.sessionState('1', {agents: [], panes: []});
    assert.equal(silent.key, 'done', 'tmux silence alert marks a quiet live agent done');
    assert.equal(api.sessionStateHtml(silent), '', 'tmux silence done state does not render the removed done tab badge');
    api.setTmuxSignalStateForTest({
      windows: [{
        key: '1:0',
        session: '1',
        window_index: '0',
        activity_ts: 10,
        bell_flag: true,
        silence_flag: false,
        panes: [{
          window_key: '1:0',
          session: '1',
          window_index: '0',
          pane_index: '0',
          target: '%11',
          pane_id: '%11',
          current_command: 'codex',
          alternate_on: false,
          pid: 0,
          dead: true,
          dead_status: 2,
        }],
      }],
    });
    assert.equal(api.sessionState('1', {agents: [], panes: []}).key, 'needs-input', 'tmux bell alert has attention priority');
    api.setTmuxSignalStateForTest({
      windows: [{
        key: '1:0',
        session: '1',
        window_index: '0',
        activity_ts: 10,
        bell_flag: false,
        silence_flag: false,
        panes: [{
          window_key: '1:0',
          session: '1',
          window_index: '0',
          pane_index: '0',
          target: '%11',
          pane_id: '%11',
          current_command: 'node',
          title: '[ . ] Action Required | yolomux.dev8001',
          alternate_on: false,
          pid: 0,
          dead: false,
        }],
      }],
    });
    const actionRequired = api.sessionState('1', {agents: [], panes: []});
    assert.equal(actionRequired.key, 'needs-input', 'Codex action-required pane title marks the session as needing input');
    assert.equal(actionRequired.reason, 'tmux agent action required');
    api.setTmuxSignalStateForTest({
      windows: [{
        key: '1:0',
        session: '1',
        window_index: '0',
        activity_ts: 10,
        bell_flag: false,
        silence_flag: false,
        panes: [{
          window_key: '1:0',
          session: '1',
          window_index: '0',
          pane_index: '0',
          target: '%11',
          pane_id: '%11',
          current_command: 'codex',
          alternate_on: false,
          pid: 0,
          dead: true,
          dead_status: 2,
        }],
      }],
    });
    const exited = api.sessionState('1', {agents: [], panes: []});
    assert.equal(exited.key, 'done', 'dead tmux agent pane marks the session done');
    assert.equal(exited.reason, 'agent exited (status 2)');
    assert.equal(api.sessionStateHtml(exited), '', 'dead-agent done state does not render the removed done tab badge');
  });

  test('t@6347', () => {
    const api = loadYolomux('', ['1']);
    const zhHant = JSON.parse(fs.readFileSync('static/locales/zh-Hant.json', 'utf8'));
    api.i18nSetCatalogForTest('zh-Hant', zhHant);
    api.setActiveLocaleForTest('zh-Hant');
    api.registerTerminalForTest('1', {}, {readyState: 3});
    assert.equal(api.sessionState('1').reason, zhHant['state.reason.terminalConnectionClosed'], 'disconnected terminal reason is localized');
    api.registerTerminalForTest('1', {}, {readyState: 1});
    api.setAutoApproveStateForTest('1', {screen: {key: 'disconnected', text: 'failed to capture pane'}});
    assert.equal(api.sessionState('1', {}).reason, zhHant['state.reason.terminalScreenUnavailable'], 'backend capture failure maps to the localized disconnected fallback');
  });

  test('t@6359', () => {
    const api = loadYolomux('', ['1', '2', '3']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
    slots.left = api.paneStateWithTabs(['1', '__info__'], '1');
    slots.slot1 = api.paneStateWithTabs(['2'], '2');
    api.rememberFileExplorerOpenIntentForTest(false);
    api.setLayoutSlotsForTest(slots);

    assert.equal(api.itemIsBackgroundPaneTab('__info__'), true);
    assert.equal(api.itemIsBackgroundPaneTab('1'), false);
    assert.deepStrictEqual(canonical(api.backgroundTabItems()), ['__info__']);
    assert.deepStrictEqual(canonical(api.inactiveTabItems()), ['__yoagent__', '__files__', '__search_history__', '__prefs__', '__debug__', '3']);
  });

  test('t@6373', () => {
    const api = loadYolomux('', ['1']);
    const firstEditor = api.registerFileEditorLayoutItem('/repo/app/one.md');
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
    slots.left = api.paneStateWithTabs([firstEditor], firstEditor);
    slots.slot1 = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(slots);
    assert.equal(api.slotForNewFileEditorTab(), 'left');
  });

  test('t@6384', () => {
    const api = loadYolomux('', ['1']);
    assert.equal(api.pathRelativeToDirectory('/repo/app/file.txt', '/repo/app'), 'file.txt');
    assert.equal(api.pathRelativeToDirectory('/repo/app/src/file.txt', '/repo/app'), 'src/file.txt');
    assert.equal(api.pathRelativeToDirectory('/repo/app', '/repo/app'), '.');
    assert.equal(api.pathRelativeToDirectory('/repo/app/file.txt', '/'), 'repo/app/file.txt');
    assert.equal(api.pathRelativeToDirectory('/other/file.txt', '/repo/app'), '/other/file.txt');
  });

  test('t@6393', () => {
    const api = loadYolomux('', ['1']);
    assert.equal(api.splitPercentForNewItem('1', 'left'), 50);
    assert.equal(api.splitPercentForNewItem('1', 'right'), 50);
    assert.equal(api.splitPercentForNewItem('file:/repo/app/TODO.md', 'left'), 50);
    assert.equal(api.splitPercentForNewItem('file:/repo/app/TODO.md', 'right'), 50);
    assert.equal(api.splitPercentForNewItem('file:/repo/app/TODO.md', 'right', 42), 42);
    assert.equal(api.splitPercentForNewItem('__files__', 'left'), 22);
    assert.equal(api.splitPercentForNewItem('__files__', 'right'), 78);
  });

  test('t@6404', () => {
    const api = loadYolomux('', ['1']);
    const windowPanes = [
      {window: '2', window_name: 'codex', window_active: false, active: true, command: 'node', pid: 222},
      {window: '1', window_name: 'bash', window_active: false, active: true, command: 'bash', pid: 111},
      {window: '3', window_name: 'node', process_label: 'codex', process_label_pid: 3333, pid: 333, window_active: true, active: true, command: 'node'},
    ];
    assert.deepStrictEqual(canonical(api.tmuxWindowRecords(windowPanes).map(item => ({
      indexText: item.indexText,
      buttonNameLabel: item.buttonNameLabel,
      nameLabel: item.nameLabel,
      numberLabel: item.numberLabel,
      indexedButtonLabel: item.indexedButtonLabel,
      indexedNameLabel: item.indexedNameLabel,
      processLabel: item.processLabel,
      pid: item.pid,
      active: item.active,
    }))), [
      {indexText: '1', buttonNameLabel: 'bash', nameLabel: 'bash (pid=111)', numberLabel: '1', indexedButtonLabel: '1:bash', indexedNameLabel: '1:bash (pid=111)', processLabel: 'bash (pid=111)', pid: 111, active: false},
      {indexText: '2', buttonNameLabel: 'codex(2)', nameLabel: 'codex(2) (pid=222)', numberLabel: '2', indexedButtonLabel: '2:codex', indexedNameLabel: '2:codex (pid=222)', processLabel: 'codex (pid=222)', pid: 222, active: false},
      {indexText: '3', buttonNameLabel: 'codex(3)', nameLabel: 'codex(3) (pid=3333)', numberLabel: '3', indexedButtonLabel: '3:codex', indexedNameLabel: '3:codex (pid=3333)', processLabel: 'codex (pid=3333)', pid: 3333, active: true},
    ], 'P5: tmux sub-window records sort by index and disambiguate duplicate names with the window index');
    const duplicateBashRecords = api.tmuxWindowRecords([
      {window: '2', window_name: 'bash', pid: 202},
      {window: '3', window_name: 'bash', pid: 303},
      {window: '4', window_name: 'bash', pid: 404},
    ]);
    assert.deepStrictEqual(canonical(duplicateBashRecords.map(item => item.indexedButtonLabel)), ['2:bash', '3:bash', '4:bash'], 'indexed window labels do not repeat the index suffix');
    assert.deepStrictEqual(canonical(duplicateBashRecords.map(item => item.buttonNameLabel)), ['bash(2)', 'bash(3)', 'bash(4)'], 'name-only labels keep the duplicate-name disambiguation suffix');
    const windowBarHtml = api.tmuxWindowBarHtml('1', {panes: windowPanes});
    assert.ok(windowBarHtml.includes('data-tmux-window-label-mode="names"'), 'P5: normal window bars prefer names');
    assert.ok(windowBarHtml.includes('data-window-index="1"'), 'P5: window bar button targets window 1');
    assert.ok(windowBarHtml.includes('data-window-index="2"'), 'P5: window bar button targets window 2');
    assert.ok(/class="tab tmux-window-button active"[^>]*data-window-index="3"[^>]*aria-pressed="true"/.test(windowBarHtml), 'P5: active tmux sub-window button is highlighted and pressed');
    assert.ok(windowBarHtml.includes('<span class="tmux-window-name-label"><span class="tmux-window-name-text">1:bash</span></span>'), 'tmux sub-window buttons show index:name without pid');
    assert.ok(/agent-icon codex[\s\S]*tmux-window-name-text">2:codex</.test(windowBarHtml), 'AI tmux sub-window buttons lead their stable labels with the matching agent icon');
    assert.equal(windowBarHtml.includes('(pid='), false, 'tmux sub-window button labels do not show process pids');
    assert.equal(windowBarHtml.includes('3:node'), false, 'DOIT.53 P2: process-aware agent labels beat raw tmux sub-window names like node');
    assert.equal(windowBarHtml.includes('data-window-agent'), false, 'tmux sub-window buttons no longer carry per-agent color tags');
    const nowSeconds = Date.now() / 1000;
    api.setAutoApproveStateForTest('1', {agent_windows: [
      {kind: 'codex', state: 'working', window_index: 3, last_active_ts: nowSeconds, window_label: '3:codex'},
      {kind: 'codex', state: 'idle', window_index: 2, last_active_ts: nowSeconds - 120, idle_since: nowSeconds - 120, window_label: '2:codex'},
    ]});
    const visibleAgentTones = canonical(api.agentWindowVisibleTonesForTest());
    assert.deepStrictEqual(visibleAgentTones, ['working', 'attention', 'cooldown'], 'one ordered definition owns every visible agent status tone');
    assert.deepStrictEqual(canonical(api.agentWindowAggregateTonesForTest()), ['attention', 'cooldown', 'working'], 'aggregate status tones keep their one severity-ordered owner');
    for (const tone of visibleAgentTones) {
      const item = {state: tone, icon: '●', pulseActive: true};
      assert.equal(api.agentWindowVisibleToneForTest(tone), true, `${tone} belongs to the shared visible-tone set`);
      assert.equal(api.agentWindowStatusToneForItemForTest(item), tone, `${tone} reaches the shared item classifier`);
      assert.ok(api.agentWindowActivityToneWrapperClassForTest(tone).endsWith(`--${tone}`), `${tone} reaches the shared wrapper classifier`);
      assert.ok(api.agentWindowStatusDotHtmlForTest(item).includes(`status-indicator--${tone}`), `${tone} reaches the shared status-dot renderer`);
      assert.ok(api.agentWindowActivityStyleAttributeForTest(tone, item, {subwindowGlyphPulse: true}).startsWith(' style='), `${tone} reaches the shared animation-style renderer`);
    }
    assert.equal(api.agentWindowVisibleToneForTest('active'), false, 'active is not silently added to the visible status-tone set');
    assert.equal(api.agentWindowStatusToneForItemForTest({state: 'active', icon: '●'}), '', 'non-status tones are rejected by the item classifier');
    assert.equal(api.agentWindowStatusDotHtmlForTest({state: 'active', icon: '●'}), '', 'non-status tones are rejected by the status-dot renderer');
    const acknowledgingToneItem = {state: 'working', icon: '●', pulseActive: false, acknowledging: true};
    assert.equal(api.agentWindowStatusToneForItemForTest(acknowledgingToneItem), 'acknowledged', 'acknowledgement remains an overlay outside the shared visible-tone set');
    assert.ok(/status-indicator--acknowledged[^"']*status-indicator--working|status-indicator--working[^"']*status-indicator--acknowledged/.test(api.agentWindowStatusDotHtmlForTest(acknowledgingToneItem)), 'acknowledgement retains the original visible-tone shape through the shared renderer');
    const subwindowStatusHtml = api.agentWindowActivityIconHtmlForStatusForTest({kind: 'codex', state: 'working', window_index: 3}, 'codex', '1');
    assert.ok(subwindowStatusHtml.includes('agent-window-activity--subwindow'), 'the live sub-window renderer marks its wrapper once for every surface');
    const parentStatusHtml = api.agentWindowActivityIconHtmlForStatusForTest({kind: 'codex', state: 'working', window_index: 3}, 'codex', '1', {statusOnly: true, subwindowGlyphPulse: false});
    assert.equal(parentStatusHtml.includes('agent-window-activity--subwindow'), false, 'aggregate parent balls do not inherit sub-window play/stop/pause geometry');
    const sampleSubwindowStatusHtml = api.agentWindowActivityIconHtmlForStatusForTest({kind: 'codex', state: 'working', window_index: 3}, 'codex', '1', {statusOnly: true, subwindowGlyphPulse: true});
    assert.ok(sampleSubwindowStatusHtml.includes('agent-window-activity--subwindow'), 'status-only sub-window samples opt into the same renderer-owned geometry class');
    const sharedToneSample = canonical(api.agentWindowStatusSampleItemForTest('attention', {pulse: true, label: 'Attention'}));
    assert.equal(sharedToneSample.state, 'attention', 'the shared sample adapter uses the live tone classifier');
    assert.equal(sharedToneSample.pulseActive, true, 'the shared sample adapter carries pulse state into the live renderer');
    assert.equal(sharedToneSample.label, 'Attention', 'the shared sample adapter carries its accessible label');
    const sharedSubwindowDot = api.agentWindowStatusDotHtmlForToneForTest('working', {surface: 'subwindow', pulse: true, label: 'Working'});
    assert.ok(sharedSubwindowDot.includes('agent-window-status-dot--subwindow-pulse'), 'the shared tone adapter emits live sub-window pulse modifiers');
    assert.ok(sharedSubwindowDot.includes('role="img"') && sharedSubwindowDot.includes('aria-label="Working"'), 'the shared tone adapter preserves accessible sample labels');
    const topbarToneHtml = api.topbarActivityCountBallHtmlForTest(2, 'attention', 'topbar-activity-ask');
    assert.ok(topbarToneHtml.includes(api.agentWindowStatusDotHtmlForToneForTest('attention', {surface: 'topbar', pulse: false})), 'topbar count balls contain the exact shared live-renderer markup');
    assert.ok(api.keyboardLegendStatusSampleForTest('cooldown', '●', {glyph: true}).includes('agent-window-activity--subwindow'), 'keyboard play/stop/pause samples use the renderer-owned sub-window surface class');
    const preferencesToneHtml = api.preferencesStatusPulseExampleHtmlForTest();
    assert.ok(preferencesToneHtml.includes('aria-label="Status pulse"') && preferencesToneHtml.includes('aria-label="Acknowledged status"'), 'Preferences status samples use translated labels through the shared adapter');
    assert.equal(api.agentWindowActivityIconForTest('codex', 'working', 0).icon, '●', 'working AI windows use the shared working icon');
    assert.equal(api.agentWindowActivityIconForTest('claude', 'idle', 60), null, 'idle AI windows show the agent glyph only, not a black/hollow dot');
    assert.equal(api.agentWindowActivityIconForTest('claude', 'idle', 10), null, 'recent idle AI windows do not show an idle icon yet');
    assert.equal(api.agentWindowActivityIconForTest('shell', 'working', 300), null, 'non-AI windows do not show working or idle icons');
    const transitionKey = '1:3:codex';
    api.setWorkflowTransitionGlowSecondsForTest(60);
    const freshWorking = api.agentWindowActivityIconForTest('codex', 'working', 0, {transitionKey, nowSeconds: 1000, scheduleRefresh: false});
    assert.equal(freshWorking.state, 'working', 'working transition state is recorded');
    assert.equal(freshWorking.pulseActive, true, 'a newly visible working ball glows during the workflow transition glow duration');
    assert.equal(freshWorking.transitionPulseActive, true, 'a newly visible working ball pulses during the workflow transition glow duration');
    const staleWorking = api.agentWindowActivityIconForTest('codex', 'working', 0, {transitionKey, nowSeconds: 1065, scheduleRefresh: false});
    assert.equal(staleWorking.pulseActive, true, 'working/play keeps glowing while the agent is still working');
    assert.equal(staleWorking.transitionPulseActive, true, 'working/play keeps pulsing while the agent is still working');
    const alwaysPulsingGreenHtml = api.agentWindowActivityIconHtmlForStatusForTest({kind: 'codex', state: 'working', window_index: 8, window_label: '8:codex'}, 'codex', '1');
    assert.ok(/status-indicator--working[^"]*agent-window-status-dot--transition-glow|agent-window-status-dot--transition-glow[^"]*status-indicator--working/.test(alwaysPulsingGreenHtml), 'green working status uses the independent transition-pulse class even when broad status animation is disabled');
    const greenToAskKey = '1:8:green-to-ask';
    assert.equal(api.agentWindowActivityIconForTest('codex', 'working', 0, {transitionKey: greenToAskKey, nowSeconds: 2000, scheduleRefresh: false}).pulseActive, true, 'green work starts in the continuously pulsing state');
    const greenToAsk = api.agentWindowActivityIconForTest('codex', 'approval', 0, {transitionKey: greenToAskKey, attention_key: 'green-to-ask-attention', attention_acknowledged: false, nowSeconds: 2001, scheduleRefresh: false});
    assert.equal(greenToAsk.state, 'attention', 'working can transition directly to the red ASK state');
    assert.equal(greenToAsk.pulseActive, true, 'green-to-red ASK starts the configured opacity pulse');
    assert.equal(greenToAsk.transitionPulseActive, true, 'green-to-red ASK uses the shared transition pulse class');
    const settledGreenToAsk = api.agentWindowActivityIconForTest('codex', 'approval', 0, {transitionKey: greenToAskKey, attention_key: 'green-to-ask-attention', attention_acknowledged: false, nowSeconds: 2065, scheduleRefresh: false});
    assert.equal(settledGreenToAsk.state, 'attention', 'red ASK remains present after the configured pulse period');
    assert.equal(settledGreenToAsk.pulseActive, false, 'red ASK becomes steady when its configured pulse period ends');
    const acknowledgedGreenToAsk = api.agentWindowActivityIconForTest('codex', 'approval', 0, {transitionKey: greenToAskKey, attention_key: 'green-to-ask-attention', attention_acknowledged: true, nowSeconds: 2066, scheduleRefresh: false});
    assert.equal(acknowledgedGreenToAsk.acknowledged, true, 'explicit acknowledgement removes the red ASK marker after its acknowledgement delay');
    const freshStopped = api.agentWindowActivityIconForTest('codex', 'idle', 0, {transitionKey, nowSeconds: 1005, scheduleRefresh: false});
    assert.equal(freshStopped.state, 'cooldown', 'a window that just stopped working shows yellow');
    assert.equal(freshStopped.pulseActive, true, 'a fresh stopped marker glows during the configured glow window');
    assert.equal(freshStopped.transitionPulseActive, true, 'a green-to-yellow transition pulses during the workflow transition glow duration');
    assert.equal(api.agentWindowActivityIconForTest('codex', 'idle', 20, {transitionKey, nowSeconds: 1020, scheduleRefresh: false}).state, 'cooldown', 'the stopped marker stays yellow during the dedicated cooldown instead of using file-recency timing');
    const staleStopped = api.agentWindowActivityIconForTest('codex', 'idle', 0, {transitionKey, nowSeconds: 1065, scheduleRefresh: false});
    assert.equal(staleStopped.state, 'cooldown', 'after the glow duration the stopped marker stays yellow until acknowledgement');
    assert.equal(staleStopped.pulseActive, false, 'after the glow duration the stopped marker becomes static');
    assert.equal(staleStopped.transitionPulseActive, false, 'after the workflow transition glow duration the yellow transition pulse stops');
    api.setWorkflowTransitionGlowSecondsForTest(0);
    const stickyTransitionKey = '1:4::codex';
    assert.equal(api.agentWindowActivityIconForTest('codex', 'working', 0, {transitionKey: stickyTransitionKey, nowSeconds: 3000, scheduleRefresh: false}).state, 'working', 'working clears an earlier yellow acknowledgement');
    const noGlowStopped = api.agentWindowActivityIconForTest('codex', 'idle', 0, {transitionKey: stickyTransitionKey, nowSeconds: 3005, scheduleRefresh: false});
    assert.equal(noGlowStopped.state, 'cooldown', '0-second glow duration keeps the stopped marker visible instead of disabling it');
    assert.equal(noGlowStopped.pulseActive, false, '0-second glow duration renders the stopped marker static from the start');
    assert.equal(noGlowStopped.transitionPulseActive, false, '0-second workflow transition glow duration keeps the yellow marker static from the start');
    assert.equal(api.agentWindowActivityIconForTest('codex', 'idle', 0, {transitionKey: stickyTransitionKey, nowSeconds: 9999, scheduleRefresh: false}).state, 'cooldown', '0-second glow duration stays visible forever until acknowledgement');
    assert.equal(api.acknowledgeAgentWindowStoppedTransitionForTest(stickyTransitionKey, null, {refresh: false}), true, 'acknowledging the matching stopped window clears the sticky yellow notification');
    const acknowledgedStopped = api.agentWindowActivityIconForTest('codex', 'idle', 0, {transitionKey: stickyTransitionKey, nowSeconds: 10000, scheduleRefresh: false});
    assert.equal(acknowledgedStopped.state, 'cooldown', 'acknowledgement retains the transition state only to suppress it until re-armed');
    assert.equal(acknowledgedStopped.acknowledged, true, 'acknowledged sticky yellow markers retain acknowledgement state for lifecycle re-arming');
    assert.equal(acknowledgedStopped.pulseActive, false, 'acknowledged sticky yellow markers do not keep glowing');
    assert.equal(acknowledgedStopped.transitionPulseActive, false, 'acknowledged sticky yellow markers do not keep transition-pulsing');
    assert.equal(api.agentWindowActivityIconForTest('codex', 'working', 0, {transitionKey: stickyTransitionKey, nowSeconds: 10010, scheduleRefresh: false}).state, 'working', 'a later working run re-arms the sticky yellow notification');
    assert.equal(api.agentWindowActivityIconForTest('codex', 'idle', 0, {transitionKey: stickyTransitionKey, nowSeconds: 10012, scheduleRefresh: false}).state, 'cooldown', 'a later stopped run shows yellow again after the earlier acknowledgement');
    api.setAutoApproveStateForTest('1', {agent_windows: [
      {kind: 'codex', state: 'idle', window_index: 7, window_label: '7:codex', working_stopped_ts: 4000},
    ]});
    assert.equal(api.acknowledgeAgentWindowActivityForTest('1', 7, {refresh: false}), true, 'clicking the matching tmux sub-window acknowledges its sticky yellow marker');
    const acknowledgedWindowStopped = api.agentWindowActivityIconForTest('codex', 'idle', 0, {session: '1', window_index: 7, working_stopped_ts: 4000, nowSeconds: 4500, scheduleRefresh: false});
    assert.equal(acknowledgedWindowStopped.state, 'cooldown', 'the acknowledged tmux sub-window retains its stopped transition identity');
    assert.equal(acknowledgedWindowStopped.acknowledging, true, 'the acknowledged tmux sub-window immediately enters the shared gray acknowledgement interval');
    const acknowledgingWindowHtml = api.agentWindowActivityIconHtmlForStatusForTest({kind: 'codex', state: 'idle', window_index: 7, working_stopped_ts: 4000}, 'codex', '1');
    assert.ok(acknowledgingWindowHtml.includes('agent-window-status-dot--acknowledging') && acknowledgingWindowHtml.includes('status-indicator--acknowledged'), 'the shared renderer turns a newly acknowledged pause glyph gray instead of removing it immediately');
    const acknowledgementSurvivesWindowSwitch = api.agentWindowActivityIconForTest('codex', 'working', 0, {session: '1', window_index: 7, pane_target: '1:7.0', nowSeconds: 4500, scheduleRefresh: false});
    assert.equal(acknowledgementSurvivesWindowSwitch.acknowledging, true, 'switching the acknowledged window to its active working capture keeps the gray marker visible for the full interval');
    assert.equal(acknowledgementSurvivesWindowSwitch.state, 'cooldown', 'the gray marker retains the acknowledged pause shape instead of changing to a green play');
    assert.equal(api.agentWindowAcknowledgementVisualActiveForTest('1:7::codex', Date.now() + 1000), true, 'the gray acknowledgement interval remains active before the configured 1550ms pulse period ends');
    assert.equal(api.agentWindowAcknowledgementVisualActiveForTest('1:7::codex', Date.now() + 1600), false, 'the gray acknowledgement interval expires after the configured status-ball pulse period');
    api.setWorkflowTransitionGlowSecondsForTest(60);
    const freshAttention = api.agentWindowActivityIconForTest('codex', 'needs-input', 0, {transitionKey, nowSeconds: 1061, scheduleRefresh: false});
    assert.equal(freshAttention.state, 'attention', 'needs-input outranks cooldown and stays on the persistent red attention state');
    assert.equal(freshAttention.pulseActive, false, 'a yellow-to-red change keeps the red marker static instead of restarting the transition pulse');
    assert.equal(freshAttention.transitionPulseActive, false, 'only green/idle-to-red transitions start the configured opacity pulse');
    const idleToAttention = api.agentWindowActivityIconForTest('codex', 'needs-input', 0, {transitionKey: 'idle-to-red', nowSeconds: 1061, scheduleRefresh: false});
    assert.equal(idleToAttention.pulseActive, true, 'an idle-to-red transition starts the configured opacity pulse');
    assert.equal(idleToAttention.transitionPulseActive, true, 'an idle-to-red transition uses the shared transition pulse class');
    const staleAttention = api.agentWindowActivityIconForTest('codex', 'needs-input', 0, {transitionKey, nowSeconds: 1125, scheduleRefresh: false});
    assert.equal(staleAttention.state, 'attention', 'red attention stays visible after its glow duration');
    assert.equal(staleAttention.pulseActive, false, 'red attention becomes static after its glow duration');
    const acknowledgedAttention = api.agentWindowActivityIconForTest('codex', 'needs-input', 0, {transitionKey: 'ack-red', attention_key: 'ack-red-key', attention_acknowledged: true, nowSeconds: 2000, scheduleRefresh: false});
    assert.equal(acknowledgedAttention.state, 'attention', 'acknowledged red attention retains its transition identity for lifecycle re-arming');
    assert.equal(acknowledgedAttention.acknowledged, true, 'acknowledged red attention is hidden by the shared renderer');
    assert.equal(acknowledgedAttention.pulseActive, false, 'acknowledged red attention does not keep glowing');
    assert.equal(acknowledgedAttention.transitionPulseActive, false, 'acknowledged red attention does not transition-pulse');
    const acknowledgedCodexHtml = api.agentWindowActivityIconHtmlForStatusForTest({kind: 'codex', state: 'needs-input', attention_key: 'ack-red-key', attention_acknowledged: true}, 'codex', '1');
    assert.ok(acknowledgedCodexHtml.includes('agent-icon codex'), 'an acknowledged Codex sub-window keeps its stable identity icon');
    assert.equal(acknowledgedCodexHtml.includes('agent-window-status-dot'), false, 'an acknowledged Codex sub-window removes its transient red marker');
    assert.equal(api.agentWindowActivityIconForTest('codex', 'approval', 0, {transitionKey, nowSeconds: 1062, scheduleRefresh: false}).state, 'attention', 'approval prompts use the same persistent red attention state');
    assert.equal(api.agentWindowActivityIconForTest('codex', 'idle', 120, {transitionKey: 'cold-idle', nowSeconds: 2000, scheduleRefresh: false}), null, 'an AI window never observed working stays glyph-only instead of showing a black idle dot');
    api.setAutoApproveStateForTest('1', {agent_windows: [
      {kind: 'claude', state: 'needs-input', window_index: 0, window_label: '0:claude'},
    ]});
    const activeAskWindowBarHtml = api.tmuxWindowBarHtml('1', {panes: [
      {window: '0', window_name: 'claude', window_active: true, active: true, command: 'claude', pid: 4444},
    ]});
    assert.deepStrictEqual(canonical(api.sessionAgentWindowStatusPayloadsForTest('1', {panes: [{window: '0', window_active: true, active: true}]}).map(agent => ({kind: agent.kind, state: agent.state, window: agent.window}))), [{kind: 'claude', state: 'needs-input', window: '0'}], 'window-bar activity resolves the matching per-window state payload');
    assert.ok(/class="tab tmux-window-button active"[\s\S]*data-window-index="0"[\s\S]*0:claude/.test(activeAskWindowBarHtml), 'active 0:claude attention window keeps its stable selected label');
    assert.ok(activeAskWindowBarHtml.includes('agent-icon claude'), activeAskWindowBarHtml);
    assert.ok(/agent-window-status-dot[\s\S]*status-indicator--attention/.test(activeAskWindowBarHtml), activeAskWindowBarHtml);
    assert.ok(activeAskWindowBarHtml.includes('0:claude'), 'selected attention windows retain their canonical window label');
    api.setAutoApproveStateForTest('1', {agent_windows: [
      {kind: 'codex', state: 'working', window_index: 0, window_label: '0:codex'},
      {kind: 'claude', state: 'idle', window_index: 1, window_label: '1:claude'},
      {kind: 'codex', state: 'approval', window_index: 2, window_label: '2:codex', screen_text: 'Would you like to run the following command?', attention_acknowledged: false},
    ]});
    const backgroundAskWindowBarHtml = api.tmuxWindowBarHtml('1', {panes: [
      {window: '0', window_name: 'node', process_label: 'codex', window_active: true, active: true},
      {window: '1', window_name: 'python3', process_label: 'claude', window_active: false, active: true},
      {window: '2', window_name: 'python3', process_label: 'codex', window_active: false, active: true},
    ]});
    const backgroundAskWindow = backgroundAskWindowBarHtml.match(/<button[^>]*data-window-index="2"[\s\S]*?<\/button>/)?.[0] || '';
    assert.ok(backgroundAskWindow.includes('agent-icon codex'), 'inactive Codex sub-windows retain their identity icon');
    assert.ok(/agent-window-status-dot[^>]*status-indicator--attention/.test(backgroundAskWindow), 'an unacknowledged approval in inactive 2:codex renders the red stop square');
    api.setAutoApproveStateForTest('1', {screen: {key: 'approval'}, agent_windows: [
      {kind: 'codex', state: 'approval', window_index: 2, window_label: '2:codex', attention_key: 'merge-priority-attention', attention_acknowledged: false, observed_ts: 100},
    ]});
    api.setTabberActivityForTest({agent_windows: {'1': [
      {kind: 'codex', state: 'idle', window_index: 2, window_label: '2:codex', observed_ts: 200},
    ]}});
    const mergedAttentionWindowBar = api.tmuxWindowBarHtml('1', {panes: [
      {window: '2', window_name: 'python3', process_label: 'codex', window_active: true, active: true},
    ]});
    const mergedAttentionParentTab = api.tmuxPaneTabHtml('1', {panes: [
      {window: '2', window_name: 'python3', process_label: 'codex', window_active: true, active: true},
    ]}, null, false);
    assert.ok(/agent-window-status-dot[^>]*status-indicator--attention/.test(mergedAttentionWindowBar), 'an unacknowledged captured ASK beats a newer idle activity snapshot in its matching sub-window');
    assert.ok(/session-agent-activity-marker[\s\S]*status-indicator--attention/.test(mergedAttentionParentTab), 'the same unacknowledged ASK reaches the parent Tab through the shared merged row');
    const repeatedAskKey = '8001:2:repeat-ask';
    const repeatedAskPayload = {agent_windows: [
      {kind: 'codex', state: 'approval', window_index: 2, window_label: '2:codex', screen_text: 'Would you like to run the following command?', attention_key: repeatedAskKey, attention_acknowledged: false},
    ]};
    api.setAutoApproveStateForTest('1', repeatedAskPayload);
    const repeatedAskPaneInfo = {panes: [
      {window: '2', window_name: 'codex', process_label: 'codex', window_active: true, active: true},
    ]};
    const repeatedAskBeforeAck = api.tmuxWindowBarHtml('1', repeatedAskPaneInfo);
    const repeatedAskParentBeforeAck = api.tmuxPaneTabHtml('1', repeatedAskPaneInfo, null, false);
    assert.ok(/agent-window-status-dot[^>]*status-indicator--attention/.test(repeatedAskBeforeAck), 'a fresh ASK displays the red stop in its sub-window');
    assert.ok(/session-agent-activity-marker[\s\S]*status-indicator--attention/.test(repeatedAskParentBeforeAck), 'a fresh ASK propagates the red stop to its parent Tab');
    api.applyAttentionAcknowledgementResponseForTest({acknowledged: [repeatedAskKey]});
    const repeatedAskOptimisticAck = api.tmuxWindowBarHtml('1', repeatedAskPaneInfo);
    const repeatedAskOptimisticParentAck = api.tmuxPaneTabHtml('1', repeatedAskPaneInfo, null, false);
    assert.equal(repeatedAskOptimisticAck.includes('agent-window-status-dot'), false, 'the acknowledgement response immediately clears the red stop while the next backend poll is pending');
    assert.ok(repeatedAskOptimisticAck.includes('agent-window-status-placeholder'), 'an acknowledged sub-window keeps an invisible play/pause/stop slot before its AI icon');
    assert.ok(repeatedAskOptimisticParentAck.includes('session-agent-activity-marker--placeholder'), 'the optimistic acknowledgement clears the parent red ball but preserves its invisible layout column');
    assert.equal(/status-indicator--attention/.test(repeatedAskOptimisticParentAck), false, 'the optimistic acknowledgement clears the parent red state');
    // A later identical prompt is a new server-owned state. Its explicit false must override the
    // local key cache so the browser never loses a visible ASK because its text was repeated.
    api.setAutoApproveStateForTest('1', repeatedAskPayload);
    const repeatedAskRearmed = api.tmuxWindowBarHtml('1', repeatedAskPaneInfo);
    const repeatedAskParentRearmed = api.tmuxPaneTabHtml('1', repeatedAskPaneInfo, null, false);
    assert.ok(/agent-window-status-dot[^>]*status-indicator--attention/.test(repeatedAskRearmed), 'a fresh backend false re-arms the same-key red stop after a prior acknowledgement');
    assert.ok(/session-agent-activity-marker[\s\S]*status-indicator--attention/.test(repeatedAskParentRearmed), 'the parent Tab re-arms with exactly the same red state as its child');
    api.setAutoApproveStateForTest('1', {agent_windows: [
      {kind: 'codex', state: 'working', window_index: 3, last_active_ts: nowSeconds, window_label: '3:codex'},
      {kind: 'codex', state: 'idle', window_index: 2, last_active_ts: nowSeconds - 120, idle_since: nowSeconds - 120, window_label: '2:codex'},
    ]});
    const windowBarWithStatusHtml = api.tmuxWindowBarHtml('1', {panes: windowPanes});
    assert.ok(/agent-icon codex[\s\S]*agent-window-status-dot[\s\S]*status-indicator--working[\s\S]*3:codex/.test(windowBarWithStatusHtml), 'working Codex windows render the Codex icon and play glyph before the stable label');
    const runWindowPanes = [
      {window: '0', window_name: 'claude', window_active: true, active: true, command: 'claude', process_label: 'claude', process_label_pid: 4444, pid: 4444},
      {window: '1', window_name: 'codex', window_active: false, active: true, command: 'codex', process_label: 'codex', process_label_pid: 5555, pid: 5555},
    ];
    api.setAutoApproveStateForTest('1', {agent_windows: [
      {kind: 'claude', state: 'idle', observed_ts: 1000, window_index: 0, window_label: '0:claude'},
      {kind: 'codex', state: 'idle', observed_ts: 1000, window_index: 1, window_label: '1:codex'},
    ]});
    api.setTabberActivityForTest({activity: {}, agent_windows: {'1': [
      {kind: 'claude', state: 'working', observed_ts: 1005, working_elapsed_seconds: 11, window_index: 0, window_label: '0:claude'},
      {kind: 'codex', state: 'working', observed_ts: 1005, working_elapsed_seconds: 0, window_index: 1, window_label: '1:codex'},
    ]}});
    const mergedRunRows = api.sessionAgentWindowStatusPayloadsForTest('1', {panes: runWindowPanes});
    assert.equal(mergedRunRows.find(row => row.kind === 'claude' && row.window_index === 0)?.state, 'working', 'newer /api/activity Claude working row overrides stale auto-approve idle row');
    assert.equal(mergedRunRows.find(row => row.kind === 'codex' && row.window_index === 1)?.state, 'working', 'newer /api/activity Codex working row overrides stale auto-approve idle row');
    const activityRunWindowBarHtml = api.tmuxWindowBarHtml('1', {panes: runWindowPanes});
    assert.ok(activityRunWindowBarHtml.includes('0:claude') && activityRunWindowBarHtml.includes('1:codex'), 'activity refresh preserves the canonical window labels');
    assert.ok(/agent-window-status-dot[\s\S]*status-indicator--working[\s\S]*agent-icon claude[\s\S]*0:claude/.test(activityRunWindowBarHtml), 'activity refresh places the Claude play glyph before its icon and canonical label');
    assert.ok(/agent-window-status-dot[\s\S]*status-indicator--working[\s\S]*agent-icon codex[\s\S]*1:codex/.test(activityRunWindowBarHtml), 'activity refresh places the Codex play glyph before its icon and canonical label');
    const manyWindows = Array.from({length: 9}, (_unused, index) => ({
      window: String(index + 1),
      window_name: `w${index + 1}`,
      window_active: index === 0,
      active: true,
      command: 'bash',
    }));
    assert.equal(api.tmuxWindowBarLabelMode(api.tmuxWindowRecords(manyWindows)), 'numbers', 'P5: many windows fall back to numeric labels');
    assert.ok(api.tmuxWindowBarHtml('1', {panes: manyWindows}).includes('data-tmux-window-label-mode="numbers"'), 'P5: numeric fallback is reflected in the rendered bar');
    assert.ok(api.tmuxWindowBarHtml('1', {panes: manyWindows}, {infoBar: true}).includes('data-tmux-window-label-mode="names"'), 'Info Bar tmux sub-window buttons keep names instead of minimizing to numbers');
    const longBranch = 'keivenchang/DIS-2239__parity-commit-link-frontend-crates';
    const longTitle = 'fix(performance): repair v1 PARITY commit + case-doc links after';
    const longMetaInfo = {
      selected_pane: {current_path: '/home/test/dynamo/dynamo4'},
      project: {
        git: {root: '/home/test/dynamo/dynamo4', cwd: '/home/test/dynamo/dynamo4', branch: longBranch, dirty_count: 1},
        pull_request: {number: 76, url: 'https://github.example/pr/76', title: longTitle, draft: true, checks: {state: 'unknown'}},
        linear: [{identifier: 'DIS-2239', state: 'In Review', title: 'Parity commit link frontend crates', url: 'https://linear.test/DIS-2239'}],
        repos: [
          {root: '/home/test/dynamo/dynamo4', cwd: '/home/test/dynamo/dynamo4', branch: longBranch, dirty_count: 1, primary: true},
          {root: '/home/test/dynamo/dynamo-utils.dev', cwd: '/home/test/dynamo/dynamo-utils.dev', branch: 'main', dirty_count: 0},
        ],
      },
    };
    const paneInfoBarMetaHtml = api.paneInfoBarMetaHtml('1', longMetaInfo);
    assert.ok(paneInfoBarMetaHtml.includes(longBranch), 'Info Bar metadata keeps the full branch name instead of inserting an ellipsis');
    assert.ok(paneInfoBarMetaHtml.includes(longTitle), 'Info Bar metadata keeps the full description instead of pre-truncating it');
    assert.equal(paneInfoBarMetaHtml.includes('...'), false, 'Info Bar metadata does not use shortText/shortBranch ellipses');
    assert.ok(/class="pane-info-bar-controls"[\s\S]*meta-repo-switch/.test(paneInfoBarMetaHtml), 'Info Bar repo selector is rendered in a fixed controls slot');
    assert.ok(/pane-info-bar-controls[\s\S]*pane-info-bar-scroll-viewport/.test(paneInfoBarMetaHtml), 'Info Bar scroll viewport starts after the fixed repo selector');
    assert.ok(/meta-sep pane-info-bar-fixed-sep" aria-hidden="true">\|<\/span>/.test(paneInfoBarMetaHtml), 'Info Bar reuses the shared compact muted metadata pipe beside repo controls');
    assert.equal(paneInfoBarMetaHtml.includes(' · '), false, 'Info Bar no longer uses wide middot separators');
    assert.ok(/pane-info-bar-scroll-viewport[\s\S]*#76 DRAFT[\s\S]*DIS-2239 In Review[\s\S]*keivenchang\/DIS-2239__parity-commit-link-frontend-crates[\s\S]*fix\(performance\): repair v1 PARITY commit \+ case-doc links after/.test(paneInfoBarMetaHtml), 'Info Bar keeps Linear immediately after the PR, before branch and path metadata');
    const idempotentInfoBarApi = loadYolomux('', ['info-scroll']);
    const idempotentMeta = idempotentInfoBarApi.testElementForId('meta-info-scroll');
    let idempotentMetaHtml = '';
    let idempotentMetaWrites = 0;
    Object.defineProperty(idempotentMeta, 'innerHTML', {
      get() { return idempotentMetaHtml; },
      set(value) {
        idempotentMetaWrites += 1;
        idempotentMetaHtml = String(value || '');
      },
    });
    idempotentInfoBarApi.updatePanelInfoBarMetaForTest('info-scroll', longMetaInfo);
    assert.equal(idempotentMetaWrites, 1, 'Info Bar writes metadata the first time');
    idempotentInfoBarApi.updatePanelInfoBarMetaForTest('info-scroll', longMetaInfo);
    assert.equal(idempotentMetaWrites, 1, 'unchanged Info Bar metadata is not reinserted and does not restart scrolling');
    api.setTranscriptInfoForTest('1', {panes: windowPanes});
    const controls = api.panelControlsHtml('1');
    assert.equal(controls.includes('data-tmux-window-bar="1"'), false, 'DOIT.53 P1: tmux pane header controls do not render the window bar');
    assert.equal(controls.includes('tmux-window-step'), false, 'P5: tmux pane controls no longer render the old prev/next stepper');
    assert.ok(controls.includes('terminal-tab'), 'DOIT.53 P1: tmux pane header keeps only the terminal tab label in the top row');
    assert.ok(controls.includes('>Term</button>'), 'DOIT.56 N3: terminal header button keeps the static Term label');
    assert.equal(controls.includes('>codex</button>') || controls.includes('>node</button>'), false, 'DOIT.56 N3: terminal header no longer duplicates active window/process names');
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    const yoloCss = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/tmuxWindowBarHtml\(session, transcriptMeta\.sessions\?\.\[session\], \{infoBar: true\}\)/.test(source), 'tmux sub-window bar is rendered on the Info Bar');
    assert.equal(/tmuxWindowBarHtml\(session, transcriptMeta\.sessions\?\.\[session\], \{infoBar: true\}\) : ''\}[\s\S]{0,120}panel-detail-close/.test(source), false, 'Info Bar no longer renders the detail-close (×) button after the sub-window bar');
    assert.ok(/delegate\(panel, 'click', '\[data-window-dir\], \[data-window-index\]'/.test(source), 'DOIT.53 P3: in-panel window buttons use the shared delegated click path');
    assert.ok(/if \(windowTarget\) \{[\s\S]*windowTarget\.dataset\.pointerActionHandled = '1'[\s\S]*handleWindowStepButtonClick\(\{target: windowTarget, currentTarget: windowTarget\}\)[\s\S]*return;[\s\S]*\}/.test(source), 'focused and unfocused pane sub-window clicks activate the original target once during pointer capture');
    assert.ok(source.includes("if (event.target?.closest?.('[data-js-debug-range-slider], .js-debug-line-chart, [data-js-debug-zoom-reset]')) return;"), 'YO!stats graph gestures bypass panel focus rerenders that replace native controls');
    assert.ok(/function tmuxWindowUserInteractionIndex\(session\)[\s\S]*querySelectorAll\('\.tmux-window-bar \.tmux-window-button\.active'\)[\s\S]*resolvedWindowIndex/.test(source), 'terminal attention acknowledgement follows the visibly active tmux sub-window when metadata is stale');
    assert.ok(/\.panel\.details-collapsed \.pane-info-bar,[\s\S]*\.panel\.details-collapsed \.panel-detail-row\s*\{[\s\S]*display:\s*none/.test(yoloCss), 'Info Bar window bar collapses with the Info Bar');
    assert.equal(yoloCss.includes('.panel-agent-badge'), false, 'DOIT.57 T1: the duplicate Info Bar agent-badge CSS is removed');
    assert.equal(source.includes('panel-agent-slot'), false, 'DOIT.57 T1: no agent-badge slot is rendered in the Info Bar');
    assert.ok(/\.tmux-window-button\.active\s*\{[\s\S]*background:\s*var\(--active-control-bg\)/.test(yoloCss), 'DOIT.57 T2: the active window button is a pressed toggle via the shared active-control tokens');
    assert.equal(/\.tmux-window-button\.active\s*\{[^}]*#[0-9a-fA-F]{3,6}/.test(yoloCss), false, 'DOIT.57 T2: the active window button uses theme-aware tokens, not hardcoded hex');
    assert.ok(/\.agent-window-activity--subwindow \.agent-icon\s*\{[\s\S]*\.agent-window-activity--subwindow \.agent-icon svg\s*\{[\s\S]*width:\s*14px[\s\S]*height:\s*14px/.test(yoloCss), 'all renderer-marked sub-window agent glyphs stay compact beside the canonical label');
    assert.ok(/\.tmux-window-button\.active \.agent-window-status-dot,\s*\.session-agent-window-block > \.session-agent-row\.current \.agent-window-status-dot\s*\{[\s\S]*--subwindow-status-glyph-border-color:\s*var\(--subwindow-status-glyph-border-color-active\)/.test(yoloCss), 'active tmux sub-window and current popover status glyphs keep a visible shared border');
    assert.ok(/\.tmux-window-button\.active \.agent-window-status-dot\.status-indicator--attention,[\s\S]*\.session-agent-row\.current \.agent-window-status-dot\.status-indicator--attention\s*\{[\s\S]*--subwindow-status-glyph-fill:\s*var\(--subwindow-status-attention-fill\)/.test(yoloCss), 'active/current attention window glyphs keep the scoped saturated red fill even on active green buttons');
    assert.ok(/\.tmux-window-bar \.tmux-window-button\.active \.agent-window-status-dot\.status-indicator--working,[\s\S]*\.session-agent-window-block > \.session-agent-row\.current \.agent-window-status-dot\.status-indicator--working\s*\{[\s\S]*--subwindow-status-glyph-fill:\s*var\(--pr-status-passing\)/.test(yoloCss), 'active working play glyphs stay green and rely on their real border for contrast');
    assert.ok(/\.session-agent-activity-marker \.agent-window-status-dot\s*\{[\s\S]*inline-size:\s*var\(--agent-status-ball-size\)[\s\S]*block-size:\s*var\(--agent-status-ball-size\)[\s\S]*border:\s*1px solid var\(--agent-status-ball-border\)[\s\S]*filter:\s*none/.test(yoloCss), 'active session-tab working balls use a real round border instead of a drop-shadow outline');
    assert.equal(/\.tmux-window-button\.active \.agent-window-status-dot[\s\S]{0,260}text-shadow:/.test(yoloCss), false, 'active tmux sub-window glyphs do not rely on text-shadow for border/clip-path shapes');
    assert.ok(/@keyframes agent-status-opacity-pulse\s*\{[\s\S]*opacity:\s*var\(--agent-status-pulse-min-opacity\)[\s\S]*opacity:\s*1/.test(yoloCss), 'active tmux sub-window activity markers inherit the shared opacity pulse in the built CSS');
    assert.equal(yoloCss.includes('window-agent-color') || yoloCss.includes('data-window-agent'), false, 'tmux sub-window buttons have no per-agent tint CSS');
    assert.ok(source.includes("workflowTransitionGlowSeconds = initialSetting('performance.workflow_transition_glow_seconds')"), 'workflow transition glow initializes from the persisted setting');
    assert.ok(source.includes("workflowTransitionGlowSeconds = numberSetting('performance.workflow_transition_glow_seconds')"), 'workflow transition glow live-updates from settings changes');
    assert.ok(source.includes("agentStatusPulsePeriodMs = initialSetting('performance.agent_status_pulse_period_ms')"), 'status pulse period initializes from the persisted setting');
    assert.ok(source.includes("agentStatusPulsePeriodMs = numberSetting('performance.agent_status_pulse_period_ms')"), 'status pulse period live-updates from settings changes');
    assert.ok(source.includes("if (key === 'cooldown') return 'cooldown'"), 'agent window stopped state maps to the shared cooldown tone instead of red attention');
    assert.ok(yoloCss.includes('.status-indicator--cooldown') && yoloCss.includes('var(--agent-status-cooldown)'), 'cooldown dot uses the dedicated vibrant agent yellow token');
    assert.ok(/\.agent-window-agent-icon--active\s*\{[^}]*animation-name:\s*agent-symbol-glow-cadence/.test(yoloCss), 'the --active current agent glyph uses the glow-cadence; working renders a static symbol plus a pulsing green play glyph');
    assert.ok(source.includes("tone !== 'active' && !agentWindowVisibleTone(tone)"), 'agent status wrappers receive the shared animation phase style through the shared visible-tone classifier');
    assert.equal(/agent-symbol-status-alternate|agent-status-dot-alternate|--agent-alternate-animation-delay|--agent-alternate-pulse-duration/.test(yoloCss + source), false, 'built assets do not include the old alternating symbol/ball implementation');
    assert.ok(source.includes("status-indicator--cooldown', pulseEnabled ? 'heartbeat-pulse'") && source.includes("pulseEnabled ? 'attention-pulse'"), 'cooldown tone opts into heartbeat in the built source only when status pulse is enabled');
    assert.ok(/\.status-indicator--working\s*\{[^}]*--attention-ring-rgb:\s*var\(--agent-status-working-rgb\)/.test(yoloCss), 'working dots use the shared green ring RGB token in the built CSS');
    assert.ok(/\.status-indicator--attention\s*\{[^}]*--attention-ring-rgb:\s*var\(--agent-status-attention-ring-rgb\)/.test(yoloCss), 'attention dots use the shared red ring RGB token in the built CSS');
    assert.ok(/\.status-indicator--cooldown\s*\{[^}]*--attention-ring-rgb:\s*var\(--agent-status-cooldown-rgb\)/.test(yoloCss), 'cooldown dots use the vibrant yellow glow in the built CSS');
    assert.equal((yoloCss.match(/255 51 71/g) || []).length, 1, 'the built CSS contains the red ring RGB tuple only at its token owner');
    assert.equal((yoloCss.match(/82 210 115/g) || []).length, 1, 'the built CSS contains the green ring RGB tuple only at its token owner');
    assert.equal(yoloCss.includes('245 197 66'), false, 'the built CSS omits the stale cooldown RGB tuple');
    assert.equal(/status-indicator--dot\.status-indicator--working\.heartbeat-pulse[\s\S]{0,240}animation-direction:\s*alternate/.test(yoloCss), false, 'working dots no longer double the pulse period with alternate direction');
    assert.ok(/\.pane-info-bar \.tmux-window-bar,[\s\S]*\.panel-detail-row \.tmux-window-bar\s*\{[\s\S]*order:\s*-1[\s\S]*flex:\s*0 0 auto[\s\S]*max-width:\s*none[\s\S]*margin-inline-start:\s*0[\s\S]*justify-content:\s*flex-start/.test(yoloCss), 'Info Bar tmux sub-window bar left-aligns (order:-1) without shrinking');
    assert.ok(source.includes('function tmuxStatusToggleHtml(session)') && source.includes('data-tmux-status-toggle'), 'Info Bar renders one shared native tmux status toggle');
    assert.ok(source.includes('function cycleTmuxStatusMode(session)') && source.includes('/api/tmux-status?session='), 'the shared Info Bar toggle reads and cycles native tmux status through one API');
    assert.ok(/\.tmux-status-toggle\s*\{[\s\S]*flex:\s*0 0 auto[\s\S]*margin-inline-start:\s*4px/.test(yoloCss), 'tmux status toggle owns a compact right-side Info Bar slot');
    assert.ok(/\.pane-info-bar-meta\.pane-info-bar-meta-overflow \.pane-info-bar-scroll-text\s*\{[\s\S]*animation-name:\s*pane-info-bar-scroll[\s\S]*animation-delay:\s*0s[\s\S]*animation-timing-function:\s*var\(--pane-info-bar-scroll-timing\)[\s\S]*animation-direction:\s*normal/.test(yoloCss), 'overflowing Info Bar metadata holds at the start, scrolls forward, holds at the end, then repeats');
    assert.ok(/@keyframes pane-info-bar-scroll\s*\{[\s\S]*transform:\s*translateX\(var\(--pane-info-bar-scroll-offset\)\)/.test(yoloCss), 'Info Bar metadata scroll uses a precomputed negative offset that animates in browsers');
    assert.equal(yoloCss.includes('translateX(calc(-1 * var(--pane-info-bar-scroll-distance)))'), false, 'Info Bar metadata scroll does not use unsupported calc multiplication in transform');
    assert.ok(source.includes("'--pane-info-bar-scroll-offset', `${-scrollDistance}px`"), 'Info Bar overflow sync stores the negative transform offset');
    assert.ok(source.includes('observePaneInfoBarResizeTarget(text)'), 'Info Bar overflow sync observes text width changes from font/layout updates');
    assert.ok(source.includes('Math.abs(previousDistance - distance) <= 4'), 'Info Bar scroll keeps the same animation track through tiny layout jitter');
    assert.ok(source.includes('if (changed) schedulePaneInfoBarMetaOverflowSync(meta)'), 'unchanged Info Bar metadata does not reschedule and restart scrolling on every metadata poll');
    assert.ok(source.includes('agentWindowActivityMarkupSignature(existing.outerHTML)') && source.includes('agentWindowActivityMarkupSignature(html)') && source.includes('if (existingSignature === nextSignature) return;'), 'phase-only Info Bar differences do not replace unchanged tmux sub-window buttons or reset sibling scrolling');
    assert.equal(/\.pane-info-bar-meta\.pane-info-bar-meta-overflow \.pane-info-bar-scroll-text\s*\{[\s\S]*animation-direction:\s*alternate/.test(yoloCss), false, 'overflowing Info Bar metadata does not reverse-scroll back to the start');
    assert.ok(source.includes('const PANE_INFO_BAR_SCROLL_START_HOLD_SECONDS = 3'), 'Info Bar metadata scroll holds the beginning for three seconds');
    assert.ok(source.includes('const PANE_INFO_BAR_SCROLL_END_HOLD_SECONDS = 2'), 'Info Bar metadata scroll holds the end for two seconds');
    assert.ok(source.includes('--pane-info-bar-scroll-timing'), 'Info Bar metadata scroll computes a per-distance timing function');
    assert.ok(/\.yolomux-dockview \.dockview-panel-content > \.panel\.dockview-inner-head-collapsed\.details-collapsed\s*\{\s*grid-template-rows:\s*minmax\(0, 1fr\)/.test(yoloCss), '2026-06-11 Info Bar regression: Dockview terminals get one full-height grid row when both inner header and details are hidden');
    assert.ok(/function setPanelDetailsCollapsed\(panel, collapsed\)\s*\{[\s\S]*schedulePanelDetailsFit\(panel\)/.test(source), '2026-06-11 Info Bar regression: details toggle refits visible tmux terminals after row height changes');
    assert.equal(source.includes('function windowStepButtonHtml'), false, 'DOIT.56 N3: dead header tmux stepper renderer stays removed');
    assert.equal(/button\.textContent = terminalTabLabel/.test(source), false, 'DOIT.56 N3: metadata refresh no longer rewrites the static terminal tab label');
    const lateApi = loadYolomux('', ['late']);
    const latePanel = lateApi.testElementForId('panel-late');
    const lateDetailRow = new TestElement('', 'div');
    lateDetailRow.className = 'pane-info-bar panel-detail-row';
    const lateClose = new TestElement('', 'button');
    lateClose.className = 'panel-detail-close';
    lateDetailRow.appendChild(lateClose);
    latePanel.appendChild(lateDetailRow);
    assert.equal(lateDetailRow.querySelectorAll('[data-tmux-window-bar="late"]').length, 0, 'late metadata panel starts without a tmux sub-window bar');
    lateApi.setTranscriptInfoForTest('late', {panes: [
      {target: 'late:0.0', window: '0', window_name: 'claude', window_active: true, active: true, process_label: 'claude', command: 'claude', current_path: '/repo/agent'},
      {target: 'late:1.0', window: '1', window_name: 'bash', window_active: false, active: true, process_label: 'bash', command: 'bash', current_path: '/repo/shell'},
    ]});
    lateApi.updatePanelWindowStepButtonsForTest('late', lateApi.transcriptInfoForTest('late'));
    const lateBars = lateDetailRow.querySelectorAll('[data-tmux-window-bar="late"]');
    assert.equal(lateBars.length, 1, 'late transcript metadata inserts one tmux sub-window bar');
    assert.deepStrictEqual(
      Array.from(lateBars[0].querySelectorAll('[data-window-index]')).map(button => button.dataset.windowIndex),
      ['0', '1'],
      'late transcript metadata inserts the tmux sub-window bar instead of leaving the Info Bar stuck without window buttons',
    );
    lateApi.updatePanelWindowStepButtonsForTest('late', {panes: []});
    assert.equal(lateDetailRow.querySelectorAll('[data-tmux-window-bar="late"]').length, 0, 'empty window metadata removes the stale tmux sub-window bar');
    const calls = [];
    const button1 = tmuxWindowButtonElement('1', '1', false);
    const button3 = tmuxWindowButtonElement('1', '3', true);
    api.testElementForId('body').appendChild(tmuxWindowBarElement('1', [button1, button3]));
    api.setFetchForTest((url, options = {}) => {
      calls.push({url: String(url), method: options.method || 'GET'});
      return new Promise(() => {});
    });
    api.tmuxWindowForTest('1', {windowIndex: '1'}, 'tmux sub-window 1:bash');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromElement(api.testElementForId('body')), ['1'], 'direct window clicks mark the clicked button active synchronously before POST resolution');
    assert.equal(tmuxWindowButtonFromElement(api.testElementForId('body'), '1')?.getAttribute('aria-pressed'), 'true', 'direct window clicks sync aria-pressed before POST resolution');
    assert.equal(tmuxWindowButtonFromElement(api.testElementForId('body'), '3')?.classList.contains('active'), false, 'direct window clicks clear the previous active button synchronously');
    assert.equal(tmuxWindowButtonFromElement(api.testElementForId('body'), '3')?.getAttribute('aria-pressed'), 'false', 'direct window clicks clear the previous pressed state synchronously');
    assert.deepStrictEqual(calls, [{url: '/api/tmux-window?session=1&window=1', method: 'POST'}], 'P5: clicking a window button posts direct select-window for that index');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromHtml(api.tmuxWindowBarHtml('1', {panes: windowPanes})), ['1'], 'stale interim renders keep the explicit target highlighted until read-back confirms');
    calls.length = 0;
    const directMetaInfo = {
      agents: [{kind: 'codex', pane_target: 'meta-preview:0.0'}],
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/home/u'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/home/u'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'bash', command: 'bash', current_path: '/tmp/shell'},
      ],
      project: {
        git: {root: '/repo/agent', cwd: '/repo/agent/src', branch: 'agent-work', dirty_count: 8}, pull_request: null, linear: [],
        repos: [{root: '/repo/agent', cwd: '/repo/agent/src', branch: 'agent-work', dirty_count: 8, primary: true}],
      },
    };
    api.setTranscriptInfoForTest('meta-preview', directMetaInfo);
    const metaNode = api.testElementForId('meta-meta-preview');
    metaNode.innerHTML = 'stale';
    api.tmuxWindowForTest('meta-preview', {windowIndex: '1'}, 'tmux sub-window 1:bash');
    assert.notEqual(metaNode.innerHTML, 'stale', 'clicking a known tmux sub-window updates path/repo metadata without waiting for the next transcript poll');
    assert.ok(metaNode.innerHTML.includes('/tmp/shell'), 'known target-window pane path is reflected immediately in the Info Bar');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: bash', 'terminal detail label follows the optimistic target window, not stale backend-active metadata');
    assert.deepStrictEqual(calls, [{url: '/api/tmux-window?session=meta-preview&window=1', method: 'POST'}], 'tmux sub-window click still posts the authoritative select-window request');
    const relativeApi = loadYolomux('', ['meta-preview']);
    relativeApi.setTranscriptInfoForTest('meta-preview', {
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/agent/src'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/agent/src'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'bash', command: 'bash', current_path: '/tmp/shell'},
      ],
    });
    const terminalFrames = [];
    relativeApi.registerTerminalForTest('meta-preview', {focus() {}}, {readyState: WebSocket.OPEN, send(message) { terminalFrames.push(JSON.parse(message)); }});
    relativeApi.setFetchForTest((url, options = {}) => {
      if (String(url).startsWith('/api/fs/batch')) {
        const requests = JSON.parse(options.body || '{}').requests || [];
        return Promise.resolve(jsonResponse({responses: requests.map(request => ({id: request.id, ok: true, status: 200, payload: {path: request.path, entries: []}}))}));
      }
      return Promise.resolve(jsonResponse({entries: [], path: '/repo/agent/src'}));
    });
    const relativeMetaNode = relativeApi.testElementForId('meta-meta-preview');
    const numericButton0 = tmuxWindowButtonElement('meta-preview', '0', true);
    const numericButton1 = tmuxWindowButtonElement('meta-preview', '1', false);
    relativeApi.testElementForId('body').appendChild(tmuxWindowBarElement('meta-preview', [numericButton0, numericButton1]));
    relativeMetaNode.innerHTML = 'stale';
    assert.equal(relativeApi.handleTerminalDataForTest('meta-preview', '\x02n'), true, 'Ctrl-b n terminal bytes are still accepted by the transport path');
    assert.equal(terminalFrames.at(-1).data, '\x02n', 'Ctrl-b n is still sent verbatim to tmux');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromElement(relativeApi.testElementForId('body')), [], 'Ctrl-b n clears the current active button synchronously until tmux confirms the real window');
    assert.equal(tmuxWindowButtonFromElement(relativeApi.testElementForId('body'), '0')?.getAttribute('aria-pressed'), 'false', 'Ctrl-b n clears aria-pressed synchronously');
    assert.equal(tmuxWindowButtonFromElement(relativeApi.testElementForId('body'), '1')?.classList.contains('active'), false, 'Ctrl-b n does not guess the next active button locally');
    assert.equal(relativeMetaNode.innerHTML, 'stale', 'Ctrl-b n does not locally predict path/repo metadata');
    relativeMetaNode.innerHTML = 'stale again';
    assert.equal(relativeApi.handleTerminalDataForTest('meta-preview', '\x02'), true, 'a split Ctrl-b prefix is still sent verbatim');
    assert.equal(relativeApi.handleTerminalDataForTest('meta-preview', '1'), true, 'a split tmux numeric selection key is still sent verbatim');
    assert.deepStrictEqual(terminalFrames.slice(-2).map(frame => frame.data), ['\x02', '1'], 'split tmux prefix and digit are not swallowed or merged');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromElement(relativeApi.testElementForId('body')), ['1'], 'Ctrl-b then a number marks that explicit target active synchronously');
    assert.equal(tmuxWindowButtonFromElement(relativeApi.testElementForId('body'), '1')?.getAttribute('aria-pressed'), 'true', 'Ctrl-b numeric selection syncs aria-pressed synchronously');
    assert.equal(tmuxWindowButtonFromElement(relativeApi.testElementForId('body'), '0')?.classList.contains('active'), false, 'Ctrl-b numeric selection clears the previous active button synchronously');
    assert.notEqual(relativeMetaNode.innerHTML, 'stale again', 'Ctrl-b then a number updates known target-window path/repo metadata immediately');
    assert.ok(relativeMetaNode.innerHTML.includes('/tmp/shell'), 'Ctrl-b numeric target uses the known target-window pane path immediately');
    const tmuxPrefixObserver = source.slice(source.indexOf('function observeTerminalTmuxPrefixWindowSwitches(session, data)'), source.indexOf('function handleTerminalData(session, data, options = {})'));
    assert.ok(tmuxPrefixObserver.includes("char === '\\x02'") && tmuxPrefixObserver.includes('terminalTmuxPrefixWindowShortcut(char)'), 'terminal transport observes tmux prefix window shortcuts without owning the bytes');
    assert.equal(source.includes('function previewTmuxWindowInfo'), false, 'tmux sub-window switching has no relative-index predictor');
    assert.equal(source.includes('function previewTmuxWindowLabel'), false, 'tmux sub-window switching has no optimistic local label repaint');
    assert.ok(/function noteTerminalTmuxWindowSwitch\(session, shortcut\)[\s\S]*const sequence = directIndex !== null[\s\S]*setTmuxWindowActiveIndexOverride\(session, directIndex\)[\s\S]*setTmuxWindowActiveIndexPending\(session\)[\s\S]*expectedIndex: directIndex, sequence[\s\S]*previousIndex, sequence/.test(source), 'terminal prefix observer highlights explicit targets and carries a sequence through readback');
    assert.ok(/function handleTerminalData\(session, data, options = \{\}\)[\s\S]*observeTerminalTmuxPrefixWindowSwitches\(session, filtered\);[\s\S]*socket\.send\(JSON\.stringify\(\{type: 'input', data: filtered\}\)\)/.test(source), 'tmux prefix observation happens before sending the unchanged terminal bytes');
    assert.ok(/const sequence = setTmuxWindowActiveIndexOverride\(session, directIndex\)[\s\S]*apiFetchJson\(`\/api\/tmux-window\?session=\$\{encodeURIComponent\(session\)\}&window=\$\{encodeURIComponent\(String\(directIndex\)\)\}`[\s\S]*tmuxWindowSwitchSequenceMatches\(session, sequence\)[\s\S]*scheduleTmuxWindowReadback\(session, \{delayMs: 0, clearActiveIndexOverride: true, expectedIndex: directIndex, sequence\}\)/.test(source), 'direct window buttons highlight before POST and keep the optimistic target until authoritative confirmation');
    assert.ok(/function setTmuxWindowActiveIndexOverride\(session, windowIndex, options = \{\}\)[\s\S]*applyTmuxWindowActiveIndexToTranscriptInfo\(String\(session\), indexKey, \{render: true\}\)/.test(source), 'known direct tmux targets overlay transcript metadata immediately so stale polls do not flash the old window');
    assert.ok(/async function applySessionMetadataPayload\(payload, options = \{\}\)[\s\S]*transcriptMeta = transcriptPayloadWithTmuxWindowOverrides\(payload\)/.test(source), 'incoming session metadata payloads preserve pending direct-window overrides');
    assert.ok(/function transcriptPayloadWithPriorSessionMetadata\(payload, previousPayload = transcriptMeta\)[\s\S]*mergeSessionMetadataDuringLightweightRefresh/.test(source), 'incoming lightweight transcript payloads preserve prior repo metadata so YO!info does not flash empty');
    assert.ok(/async function refreshTmuxWindowActiveFromSignals\(session, options = \{\}\)[\s\S]*apiFetchJson\(tmuxWindowSignalReadbackUrl\(session\)/.test(source), 'tmux sub-window readback uses the session-scoped lightweight tmux-signals endpoint');
    assert.ok(/function setTmuxWindowActiveIndexOverride\(session, windowIndex, options = \{\}\)[\s\S]*refreshTabberPanelsForTmuxWindowChange\(\)/.test(source), 'Tabber repaints immediately when a known tmux sub-window target is selected');
    assert.ok(/function setTmuxWindowActiveIndexPending\(session, options = \{\}\)[\s\S]*refreshTabberPanelsForTmuxWindowChange\(\)/.test(source), 'Tabber repaints immediately when an unknown tmux sub-window target is pending');
    assert.ok(/function applyTmuxSignalActiveWindowsToTranscriptInfo\(payload = \{\}\)[\s\S]*updatePanelHeader\(session, transcriptMeta\.sessions\?\.\[session\]\)[\s\S]*renderInfoPanel\(\);[\s\S]*refreshTabberPanelsForTmuxWindowChange\(\)/.test(source), 'probe-confirmed tmux sub-window readback repaints visible Tabber panels without waiting for the activity poll');
  });

  await testAsync('lightweight transcript refresh keeps existing YO!info branch rows', async () => {
    const api = loadYolomux('', ['meta-preview']);
    const fullInfo = {
      session: 'meta-preview',
      panes: [{target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/app'}],
      agents: [],
      window_metadata: [{window_index: '0', git: {root: '/repo/app', branch: 'feature/yoinfo'}}],
      project: {
        git: {
          root: '/repo/app',
          branch: 'feature/yoinfo',
          other_branches: {
            branches: [
              {name: 'feature/yoinfo', current: true, updated: 'now', updated_ts: 500, subject: 'keep branch metadata visible'},
            ],
          },
        },
        repos: [],
        pull_request: null,
        linear: [],
      },
      metadata_loading: false,
    };
    await api.applyTranscriptsPayloadForTest({session_order: ['meta-preview'], sessions: {'meta-preview': fullInfo}}, {refreshAuto: false, refreshContext: false, refreshActivity: false});
    assert.deepStrictEqual(canonical(api.infoBranchRows().map(row => row.branch)), ['feature/yoinfo']);

    const lightweightInfo = {
      session: 'meta-preview',
      panes: [{target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/app/src'}],
      agents: [{kind: 'claude'}],
      window_metadata: [],
      project: {git: null, pull_request: null, linear: [], repos: [], loading: true},
      metadata_loading: true,
    };
    await api.applyTranscriptsPayloadForTest({metadata_loading: true, session_order: ['meta-preview'], sessions: {'meta-preview': lightweightInfo}}, {refreshAuto: false, refreshContext: false, refreshActivity: false});
    assert.deepStrictEqual(canonical(api.infoBranchRows().map(row => row.branch)), ['feature/yoinfo'], 'lightweight refresh does not blank the YO!info table');
    assert.equal(api.transcriptInfoForTest('meta-preview').metadata_loading, true, 'the merged session still records that fresh metadata is loading');
    assert.equal(api.transcriptInfoForTest('meta-preview').panes[0].current_path, '/repo/app/src', 'lightweight pane updates are still applied');

    const fullEmptyInfo = {
      ...lightweightInfo,
      project: {git: null, pull_request: null, linear: [], repos: []},
      metadata_loading: false,
    };
    await api.applyTranscriptsPayloadForTest({metadata_loading: false, session_order: ['meta-preview'], sessions: {'meta-preview': fullEmptyInfo}}, {refreshAuto: false, refreshContext: false, refreshActivity: false});
    assert.deepStrictEqual(canonical(api.infoBranchRows().map(row => row.branch)), [], 'a later full metadata payload can still clear rows when there is truly no branch metadata');
  });

  await testAsync('tmux sub-window direct failure rolls back optimistic metadata', async () => {
    const api = loadYolomux('', ['meta-preview']);
    const info = {
      agents: [{kind: 'codex', pane_target: 'meta-preview:0.0'}, {kind: 'claude', pane_target: 'meta-preview:1.0'}],
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/codex'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/codex'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'claude', command: 'claude', current_path: '/repo/claude'},
      ],
    };
    api.setTranscriptInfoForTest('meta-preview', info);
    api.setFetchForTest((url, options = {}) => {
      if (String(url).startsWith('/api/tmux-window')) return Promise.reject(new Error('tmux select failed'));
      if (String(url).startsWith('/api/fs/batch')) {
        const requests = JSON.parse(options.body || '{}').requests || [];
        return Promise.resolve(jsonResponse({responses: requests.map(request => ({id: request.id, ok: true, status: 200, payload: {path: request.path, entries: []}}))}));
      }
      return Promise.resolve(jsonResponse({entries: [], path: '/repo/claude'}));
    });

    api.tmuxWindowForTest('meta-preview', {windowIndex: '1'}, 'tmux sub-window 1:claude');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: claude', 'direct click applies the known target-window metadata synchronously');

    await flushAsyncWork();
    await flushAsyncWork();

    assert.equal(api.tmuxWindowActiveIndexOverrideForTest('meta-preview'), undefined, 'failed direct select clears the optimistic target');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: codex', 'failed direct select restores the previous active-window metadata');
  });

  await testAsync('tmux sub-window explicit readback ignores stale active window', async () => {
    const api = loadYolomux('', ['meta-preview']);
    const button0 = tmuxWindowButtonElement('meta-preview', '0', true);
    const button1 = tmuxWindowButtonElement('meta-preview', '1', false);
    api.testElementForId('body').appendChild(tmuxWindowBarElement('meta-preview', [button0, button1]));
    api.setTranscriptInfoForTest('meta-preview', {
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/agent/src'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'bash', command: 'bash', current_path: '/tmp/shell'},
      ],
    });
    api.registerTerminalForTest('meta-preview', {focus() {}}, {readyState: WebSocket.OPEN, send() {}});
    assert.equal(api.handleTerminalDataForTest('meta-preview', '\x021'), true, 'Ctrl-b 1 sets an explicit optimistic target');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromElement(api.testElementForId('body')), ['1'], 'explicit target is active immediately');
    api.setFetchForTest(() => Promise.resolve(jsonResponse({ok: true, windows: [{
      session: 'meta-preview',
      window_index: '0',
      active: true,
      panes: [{target: 'meta-preview:0.0', pane_id: 'meta-preview:0.0', pane_index: '0', window_index: '0', active: true, current_path: '/repo/agent/src', current_command: 'codex'}],
    }, {
      session: 'meta-preview',
      window_index: '1',
      active: false,
      panes: [{target: 'meta-preview:1.0', pane_id: 'meta-preview:1.0', pane_index: '0', window_index: '1', active: true, current_path: '/tmp/shell', current_command: 'bash'}],
    }]})));
    await api.scheduleTmuxWindowReadbackForTest('meta-preview', {delayMs: 0, clearActiveIndexOverride: true, expectedIndex: '1', attempt: 5});
    assert.deepStrictEqual(activeTmuxWindowIndexesFromElement(api.testElementForId('body')), ['1'], 'stale readback does not replace the explicit target');
    assert.equal(tmuxWindowButtonFromElement(api.testElementForId('body'), '0')?.classList.contains('active'), false, 'stale previous active window does not flash active');
    assert.equal(api.activeTmuxSignalWindowForSessionForTest('meta-preview')?.window_index, '1', 'cached tmux signals keep the explicit target active during stale direct-window readback');
  });

  await testAsync('tmux signals keep stale transcript metadata from repainting old active window', async () => {
    const api = loadYolomux('', ['meta-preview']);
    const staleInfo = {
      agents: [{kind: 'codex', pane_target: 'meta-preview:0.0'}, {kind: 'claude', pane_target: 'meta-preview:1.0'}],
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/codex'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/codex'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'claude', command: 'claude', current_path: '/repo/claude'},
      ],
    };
    api.setTranscriptInfoForTest('meta-preview', staleInfo);
    api.setFetchForTest((url, options = {}) => {
      if (String(url).startsWith('/api/fs/batch')) {
        const requests = JSON.parse(options.body || '{}').requests || [];
        return Promise.resolve(jsonResponse({responses: requests.map(request => ({id: request.id, ok: true, status: 200, payload: {path: request.path, entries: []}}))}));
      }
      if (String(url).startsWith('/api/activity')) return Promise.resolve(jsonResponse({activity: {}}));
      if (String(url).startsWith('/api/session-files')) return Promise.resolve(jsonResponse({session: 'meta-preview', files: [], repos: [], errors: [], loaded: true}));
      return Promise.resolve(jsonResponse({}));
    });
    api.applyTmuxSignalsPayloadForTest({windows: [{
      session: 'meta-preview',
      window_index: '0',
      active: false,
      panes: [{target: 'meta-preview:0.0', pane_id: 'meta-preview:0.0', pane_index: '0', window_index: '0', active: true, current_path: '/repo/codex', current_command: 'codex'}],
    }, {
      session: 'meta-preview',
      window_index: '1',
      active: true,
      panes: [{target: 'meta-preview:1.0', pane_id: 'meta-preview:1.0', pane_index: '0', window_index: '1', active: true, current_path: '/repo/claude', current_command: 'claude'}],
    }]});

    await api.applyTranscriptsPayloadForTest({session_order: ['meta-preview'], sessions: {'meta-preview': staleInfo}}, {refreshAuto: false, refreshContext: false, refreshActivity: false});

    assert.deepStrictEqual(activeTmuxWindowIndexesFromHtml(api.tmuxWindowBarHtml('meta-preview', api.transcriptInfoForTest('meta-preview'))), ['1'], 'stale transcript payloads are normalized through tmux-signals before repainting the window bar');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: claude', 'stale transcript payloads do not revert the terminal title to the old active window');
  });

  await testAsync('tmux signal windows render before transcript discovery', async () => {
    const api = loadYolomux('', ['meta-preview']);
    const info = {panes: [{window: '0', window_name: 'codex', window_active: true, active: true, process_label: 'codex'}]};
    api.setTmuxSignalStateForTest({windows: [{
      session: 'meta-preview',
      window_index: '0',
      window_name: 'codex',
      active: true,
      panes: [{pane_index: '0', target: 'meta-preview:0.0', active: true, current_command: 'codex'}],
    }, {
      session: 'meta-preview',
      window_index: '1',
      window_name: 'bash',
      active: false,
      panes: [{pane_index: '0', target: 'meta-preview:1.0', active: true, current_command: 'bash'}],
    }]});
    const html = api.tmuxWindowBarHtml('meta-preview', info);
    assert.ok(html.includes('data-window-index="0"') && html.includes('data-window-index="1"'), 'the next tmux-signals push adds a bare new window to the existing bar without waiting for transcript metadata');
  });

  await testAsync('successful tmux signal snapshots remove dead transcript sub-windows from the bar', async () => {
    const api = loadYolomux('', ['meta-preview']);
    const staleInfo = {panes: [
      {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex'},
      {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'claude', command: 'claude'},
    ]};
    api.applyTmuxSignalsPayloadForTest({
      ok: true,
      sessions: {'meta-preview': {windows: ['meta-preview:0', 'meta-preview:1']}},
      windows: [
        {key: 'meta-preview:0', session: 'meta-preview', window_index: '0', active: true, panes: [{target: 'meta-preview:0.0', pane_id: 'meta-preview:0.0', pane_index: '0', active: true, current_command: 'codex'}]},
        {key: 'meta-preview:1', session: 'meta-preview', window_index: '1', active: false, panes: [{target: 'meta-preview:1.0', pane_id: 'meta-preview:1.0', pane_index: '0', active: true, current_command: 'claude'}]},
      ],
    });
    api.applyTmuxSignalsPayloadForTest({
      patch: true,
      ok: true,
      removed_window_keys: ['meta-preview:0'],
      windows: [],
    });

    const indexes = [...api.tmuxWindowBarHtml('meta-preview', staleInfo).matchAll(/data-window-index="([^"]+)"/g)].map(match => match[1]);
    assert.deepStrictEqual(indexes, ['1'], 'a successful tmux signal removal prunes the stale 0:codex transcript pane immediately');
  });

  await testAsync('direct tmux sub-window clicks do not bounce through stale transcript or partial signal pushes', async () => {
    const api = loadYolomux('', ['meta-preview']);
    const button0 = tmuxWindowButtonElement('meta-preview', '0', true);
    const button1 = tmuxWindowButtonElement('meta-preview', '1', false);
    api.testElementForId('body').appendChild(tmuxWindowBarElement('meta-preview', [button0, button1]));
    const staleInfo = {
      agents: [{kind: 'codex', pane_target: 'meta-preview:0.0'}, {kind: 'claude', pane_target: 'meta-preview:1.0'}],
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/codex'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/codex'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'claude', command: 'claude', current_path: '/repo/claude'},
      ],
    };
    api.setTranscriptInfoForTest('meta-preview', staleInfo);
    api.setFetchForTest((url, options = {}) => {
      if (String(url).startsWith('/api/tmux-window')) return new Promise(() => {});
      if (String(url).startsWith('/api/fs/batch')) {
        const requests = JSON.parse(options.body || '{}').requests || [];
        return Promise.resolve(jsonResponse({responses: requests.map(request => ({id: request.id, ok: true, status: 200, payload: {path: request.path, entries: []}}))}));
      }
      if (String(url).startsWith('/api/activity')) return Promise.resolve(jsonResponse({activity: {}}));
      if (String(url).startsWith('/api/session-files')) return Promise.resolve(jsonResponse({session: 'meta-preview', files: [], repos: [], errors: [], loaded: true}));
      return Promise.resolve(jsonResponse({}));
    });

    api.tmuxWindowForTest('meta-preview', {windowIndex: '1'}, 'tmux sub-window 1:claude');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromElement(api.testElementForId('body')), ['1'], 'the direct target is active immediately after click');
    await api.applyTranscriptsPayloadForTest({session_order: ['meta-preview'], sessions: {'meta-preview': staleInfo}}, {refreshAuto: false, refreshContext: false, refreshActivity: false});
    assert.deepStrictEqual(activeTmuxWindowIndexesFromHtml(api.tmuxWindowBarHtml('meta-preview', api.transcriptInfoForTest('meta-preview'))), ['1'], 'a stale transcript push cannot repaint the old active tmux sub-window while a direct target is pending');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: claude', 'the terminal label stays on the direct target while stale transcript data is pending');

    api.applyTmuxSignalsPayloadForTest({windows: [{
      session: 'meta-preview',
      window_index: '0',
      active: true,
      panes: [{target: 'meta-preview:0.0', pane_id: 'meta-preview:0.0', pane_index: '0', window_index: '0', active: true, current_path: '/repo/codex', current_command: 'codex'}],
    }]});

    assert.equal(api.activeTmuxSignalWindowForSessionForTest('meta-preview'), null, 'a partial stale signal payload is treated as unconfirmed while the target window is missing');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromElement(api.testElementForId('body')), ['1'], 'partial stale signal pushes keep the direct target button active');
    assert.equal(tmuxWindowButtonFromElement(api.testElementForId('body'), '0')?.classList.contains('active'), false, 'partial stale signal pushes do not flash the old button active');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromHtml(api.tmuxWindowBarHtml('meta-preview', api.transcriptInfoForTest('meta-preview'))), ['1'], 'partial stale signal pushes do not repaint generated window bars back to the old active window');
  });

  await testAsync('direct tmux sub-window target survives stale in-place button bar refresh', async () => {
    const api = loadYolomux('', ['meta-preview']);
    const button0 = tmuxWindowButtonElement('meta-preview', '0', true);
    const button1 = tmuxWindowButtonElement('meta-preview', '1', false);
    api.testElementForId('body').appendChild(tmuxWindowBarElement('meta-preview', [button0, button1]));
    const staleInfo = {
      agents: [{kind: 'codex', pane_target: 'meta-preview:0.0'}, {kind: 'claude', pane_target: 'meta-preview:1.0'}],
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/codex'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/codex'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'claude', command: 'claude', current_path: '/repo/claude'},
      ],
    };
    api.setTranscriptInfoForTest('meta-preview', staleInfo);
    api.setFetchForTest(url => {
      if (String(url).startsWith('/api/tmux-window')) return new Promise(() => {});
      return Promise.resolve(jsonResponse({}));
    });

    api.tmuxWindowForTest('meta-preview', {windowIndex: '1'}, 'tmux sub-window 1:claude');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromElement(api.testElementForId('body')), ['1'], 'direct click marks 1:claude active before the POST settles');

    api.updatePanelWindowStepButtonsForTest('meta-preview', staleInfo);

    assert.deepStrictEqual(activeTmuxWindowIndexesFromElement(api.testElementForId('body')), ['1'], 'stale header refresh cannot replace the button bar with 0:codex active');
  });

  await testAsync('direct tmux sub-window readback only confirms from raw tmux active state', async () => {
    const api = loadYolomux('', ['meta-preview'], 'http:', 'Linux x86_64', 'admin', {fireAllTimeouts: true});
    const staleInfo = {
      agents: [{kind: 'codex', pane_target: 'meta-preview:0.0'}, {kind: 'claude', pane_target: 'meta-preview:1.0'}],
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/codex'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/codex'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'claude', command: 'claude', current_path: '/repo/claude'},
      ],
    };
    const staleSignals = {ok: true, windows: [{
      session: 'meta-preview',
      window_index: '0',
      active: true,
      panes: [{target: 'meta-preview:0.0', pane_id: 'meta-preview:0.0', pane_index: '0', window_index: '0', active: true, current_path: '/repo/codex', current_command: 'codex'}],
    }, {
      session: 'meta-preview',
      window_index: '1',
      active: false,
      panes: [{target: 'meta-preview:1.0', pane_id: 'meta-preview:1.0', pane_index: '0', window_index: '1', active: true, current_path: '/repo/claude', current_command: 'claude'}],
    }]};
    const requests = [];
    api.setTranscriptInfoForTest('meta-preview', staleInfo);
    api.setFetchForTest((url, options = {}) => {
      requests.push(String(url));
      if (String(url).startsWith('/api/tmux-window')) return Promise.resolve(jsonResponse({ok: true}));
      if (String(url).startsWith('/api/tmux-signals')) return Promise.resolve(jsonResponse(staleSignals));
      if (String(url).startsWith('/api/fs/batch')) {
        const batchRequests = JSON.parse(options.body || '{}').requests || [];
        return Promise.resolve(jsonResponse({responses: batchRequests.map(request => ({id: request.id, ok: true, status: 200, payload: {path: request.path, entries: []}}))}));
      }
      if (String(url).startsWith('/api/activity')) return Promise.resolve(jsonResponse({activity: {}}));
      if (String(url).startsWith('/api/session-files')) return Promise.resolve(jsonResponse({session: 'meta-preview', files: [], repos: [], errors: [], loaded: true}));
      return Promise.resolve(jsonResponse({}));
    });

    api.tmuxWindowForTest('meta-preview', {windowIndex: '1'}, 'tmux sub-window 1:claude');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: claude', 'the direct click still applies the optimistic target immediately');
    for (let i = 0; i < 20; i += 1) await flushAsyncWork();

    assert.ok(requests.filter(url => url.startsWith('/api/tmux-signals')).length > 1, 'stale raw tmux signals keep readback retrying instead of falsely confirming');
    assert.equal(api.tmuxWindowActiveIndexOverrideForTest('meta-preview'), '1', 'stale raw tmux signals do not clear the optimistic direct-window target');
    await api.applyTranscriptsPayloadForTest({session_order: ['meta-preview'], sessions: {'meta-preview': staleInfo}}, {refreshAuto: false, refreshContext: false, refreshActivity: false});
    assert.deepStrictEqual(activeTmuxWindowIndexesFromHtml(api.tmuxWindowBarHtml('meta-preview', api.transcriptInfoForTest('meta-preview'))), ['1'], 'the held optimistic target keeps stale metadata from repainting the old window after delayed readback');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: claude', 'the terminal tab label does not bounce back to the old process after stale delayed readback');
  });

  await testAsync('confirmed direct tmux sub-window target ignores delayed stale signal snapshots', async () => {
    const api = loadYolomux('', ['meta-preview'], 'http:', 'Linux x86_64', 'admin', {fireAllTimeouts: true});
    const staleInfo = {
      agents: [{kind: 'codex', pane_target: 'meta-preview:0.0'}, {kind: 'claude', pane_target: 'meta-preview:1.0'}],
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/codex'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/codex'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'claude', command: 'claude', current_path: '/repo/claude'},
      ],
    };
    const confirmedSignals = {ok: true, generated_at: Date.now() / 1000, windows: [{
      session: 'meta-preview',
      window_index: '0',
      active: false,
      panes: [{target: 'meta-preview:0.0', pane_id: 'meta-preview:0.0', pane_index: '0', window_index: '0', active: true, current_path: '/repo/codex', current_command: 'codex'}],
    }, {
      session: 'meta-preview',
      window_index: '1',
      active: true,
      panes: [{target: 'meta-preview:1.0', pane_id: 'meta-preview:1.0', pane_index: '0', window_index: '1', active: true, current_path: '/repo/claude', current_command: 'claude'}],
    }]};
    const oldStaleSignals = {ok: true, generated_at: 1, windows: [{
      session: 'meta-preview',
      window_index: '0',
      active: true,
      panes: [{target: 'meta-preview:0.0', pane_id: 'meta-preview:0.0', pane_index: '0', window_index: '0', active: true, current_path: '/repo/codex', current_command: 'codex'}],
    }, {
      session: 'meta-preview',
      window_index: '1',
      active: false,
      panes: [{target: 'meta-preview:1.0', pane_id: 'meta-preview:1.0', pane_index: '0', window_index: '1', active: true, current_path: '/repo/claude', current_command: 'claude'}],
    }]};
    const untimestampedStaleSignals = {...oldStaleSignals};
    delete untimestampedStaleSignals.generated_at;
    const requests = [];
    api.setTranscriptInfoForTest('meta-preview', staleInfo);
    api.setFetchForTest((url, options = {}) => {
      requests.push(String(url));
      if (String(url).startsWith('/api/tmux-window')) return Promise.resolve(jsonResponse({ok: true}));
      if (String(url).startsWith('/api/tmux-signals')) return Promise.resolve(jsonResponse(confirmedSignals));
      if (String(url).startsWith('/api/fs/batch')) {
        const batchRequests = JSON.parse(options.body || '{}').requests || [];
        return Promise.resolve(jsonResponse({responses: batchRequests.map(request => ({id: request.id, ok: true, status: 200, payload: {path: request.path, entries: []}}))}));
      }
      if (String(url).startsWith('/api/activity')) return Promise.resolve(jsonResponse({activity: {}}));
      if (String(url).startsWith('/api/session-files')) return Promise.resolve(jsonResponse({session: 'meta-preview', files: [], repos: [], errors: [], loaded: true}));
      return Promise.resolve(jsonResponse({}));
    });

    api.tmuxWindowForTest('meta-preview', {windowIndex: '1'}, 'tmux sub-window 1:claude');
    for (let i = 0; i < 12; i += 1) await flushAsyncWork();

    assert.equal(api.tmuxWindowActiveIndexOverrideForTest('meta-preview'), undefined, 'confirmed direct target can release the short pressed-button override');
    api.applyTmuxSignalsPayloadForTest(untimestampedStaleSignals);
    assert.deepStrictEqual(activeTmuxWindowIndexesFromHtml(api.tmuxWindowBarHtml('meta-preview', api.transcriptInfoForTest('meta-preview'))), ['1'], 'an untimestamped delayed tmux signal cannot repaint the previous active window after the override clears');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: claude', 'an untimestamped delayed tmux signal does not bounce the terminal label back to Codex');
    api.applyTmuxSignalsPayloadForTest(oldStaleSignals);
    await api.applyTranscriptsPayloadForTest({session_order: ['meta-preview'], sessions: {'meta-preview': staleInfo}}, {refreshAuto: false, refreshContext: false, refreshActivity: false});

    assert.deepStrictEqual(activeTmuxWindowIndexesFromHtml(api.tmuxWindowBarHtml('meta-preview', api.transcriptInfoForTest('meta-preview'))), ['1'], 'an older delayed tmux signal cannot repaint the previous active window after the override clears');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: claude', 'an older delayed tmux signal does not bounce the terminal label back to Codex');
    assert.ok(requests.some(url => url.startsWith('/api/tmux-signals')), 'the test exercised the direct-window signal readback path');
  });

  await testAsync('newer direct tmux sub-window clicks ignore older delayed readbacks', async () => {
    const api = loadYolomux('', ['meta-preview']);
    const staleInfo = {
      agents: [{kind: 'codex', pane_target: 'meta-preview:0.0'}, {kind: 'claude', pane_target: 'meta-preview:1.0'}],
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/codex'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/codex'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'claude', command: 'claude', current_path: '/repo/claude'},
      ],
    };
    const postResolves = [];
    const requests = [];
    api.setTranscriptInfoForTest('meta-preview', staleInfo);
    api.setFetchForTest((url, options = {}) => {
      requests.push(String(url));
      if (String(url).startsWith('/api/tmux-window')) {
        return new Promise(resolve => postResolves.push(() => resolve(jsonResponse({ok: true}))));
      }
      if (String(url).startsWith('/api/tmux-signals')) {
        return Promise.resolve(jsonResponse({ok: true, windows: [{
          session: 'meta-preview',
          window_index: '0',
          active: true,
          panes: [{target: 'meta-preview:0.0', pane_id: 'meta-preview:0.0', pane_index: '0', window_index: '0', active: true, current_path: '/repo/codex', current_command: 'codex'}],
        }, {
          session: 'meta-preview',
          window_index: '1',
          active: false,
          panes: [{target: 'meta-preview:1.0', pane_id: 'meta-preview:1.0', pane_index: '0', window_index: '1', active: true, current_path: '/repo/claude', current_command: 'claude'}],
        }]}));
      }
      if (String(url).startsWith('/api/fs/batch')) {
        const batchRequests = JSON.parse(options.body || '{}').requests || [];
        return Promise.resolve(jsonResponse({responses: batchRequests.map(request => ({id: request.id, ok: true, status: 200, payload: {path: request.path, entries: []}}))}));
      }
      if (String(url).startsWith('/api/activity')) return Promise.resolve(jsonResponse({activity: {}}));
      if (String(url).startsWith('/api/session-files')) return Promise.resolve(jsonResponse({session: 'meta-preview', files: [], repos: [], errors: [], loaded: true}));
      return Promise.resolve(jsonResponse({}));
    });

    api.tmuxWindowForTest('meta-preview', {windowIndex: '0'}, 'tmux sub-window 0:codex');
    api.tmuxWindowForTest('meta-preview', {windowIndex: '1'}, 'tmux sub-window 1:claude');
    assert.equal(api.tmuxWindowActiveIndexOverrideForTest('meta-preview'), '1', 'latest direct click owns the optimistic target');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: claude', 'latest direct click shows Claude before readback');

    postResolves[0]();
    for (let i = 0; i < 8; i += 1) await flushAsyncWork();

    assert.equal(api.tmuxWindowActiveIndexOverrideForTest('meta-preview'), '1', 'older POST completion cannot confirm the previous window over the latest click');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: claude', 'older delayed readback does not bounce the button label back to Codex');
    assert.equal(requests.filter(url => url.startsWith('/api/tmux-signals')).length, 0, 'stale POST completion is ignored before it can start a readback');
  });

  await testAsync('tmux sub-window relative readback lands on backend active window', async () => {
    const api = loadYolomux('', ['meta-preview']);
    api.setTranscriptInfoForTest('meta-preview', {
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/agent/src'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/agent/src'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'bash', command: 'bash', current_path: '/tmp/shell'},
      ],
    });
    const requests = [];
    api.setFetchForTest((url, options = {}) => {
      requests.push({url: String(url), method: options.method || 'GET'});
      return Promise.resolve(jsonResponse({ok: true, windows: [{
        session: 'meta-preview',
        window_index: '0',
        active: false,
        panes: [{target: 'meta-preview:0.0', pane_id: 'meta-preview:0.0', pane_index: '0', window_index: '0', active: true, current_path: '/repo/agent/src', current_command: 'codex'}],
      }, {
        session: 'meta-preview',
        window_index: '1',
        window_name: 'bash',
        active: true,
        panes: [{target: 'meta-preview:1.0', pane_id: 'meta-preview:1.0', pane_index: '0', window_index: '1', active: true, current_path: '/tmp/shell', current_command: 'bash'}],
      }]}));
    });

    await api.scheduleTmuxWindowReadbackForTest('meta-preview', {delayMs: 0});

    assert.deepStrictEqual(requests.filter(request => request.url.startsWith('/api/tmux-signals')), [{url: '/api/tmux-signals?force=1&session=meta-preview', method: 'GET'}], 'relative window navigation readback uses the session-scoped lightweight tmux signal endpoint once');
    assert.deepStrictEqual(requests.filter(request => request.url.startsWith('/api/session-metadata') || request.url.startsWith('/api/transcripts')), [], 'relative window navigation readback does not wait on session metadata');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromHtml(api.tmuxWindowBarHtml('meta-preview', api.transcriptInfoForTest('meta-preview'))), ['1'], 'relative window navigation lands on the backend window_active value');
    assert.equal(api.transcriptInfoForTest('meta-preview').selected_pane.current_path, '/tmp/shell', 'relative window navigation updates the selected pane path from tmux signals');
  });

  test('t@6424', () => {
    loadYolomux();
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    const start = source.indexOf('function showAttentionAlert(');
    const end = source.indexOf('function dismissAttentionAlertsForSession(', start);
    assert.ok(start > 0 && end > start, 'could not locate showAttentionAlert body');
    const body = source.slice(start, end);
    assert.ok(body.includes('container: displayToastContainer(session)'), 'attention notifications use the target pane-local toast stack');
    assert.equal(body.includes('container: attentionAlerts'), false, 'attention notifications do not use the global fixed stack');
    assert.ok(source.includes('function compactNotificationTitle('), 'notification/toast titles use one compact title helper');
    assert.ok(body.includes('attentionToastTitle(session, state)'), 'attention toasts name the exact session and tmux window through one title helper');
    assert.ok(body.includes('attentionToastLine(session, state)'), 'attention toasts route their body through the shared agent-status message renderer');
    assert.ok(body.includes('focusAttentionToastTarget(session, state)'), 'clicking an attention toast follows the shared direct tmux-window route');
    assert.ok(source.includes('function attentionToastLine(session, state)'), 'attention toast status content has one shared renderer');
    assert.ok(source.includes('tmuxPaneTabTokenHtml(session, {'), 'attention toast status content reuses the shared session Tab renderer');
    assert.ok(source.includes('tmuxWindowButtonHtml({'), 'attention toast status content reuses the tmux sub-window button renderer');
    assert.ok(source.includes("marker.className = 'attention-toast-controls'"), 'attention toast keeps its shared Tab and sub-window control in one wrap-around group');
    assert.ok(source.includes("classes: ['attention-toast-agent-button']"), 'attention toast status content uses the shared stop/pause-plus-agent button styling');
    assert.ok(source.includes('function pauseToastRemoval(id, node, reason)'), 'hover/focus pauses the shared toast removal timer');
    assert.ok(source.includes("node.addEventListener('pointerenter', () => pauseToastRemoval(id, node, 'Pointer'))"), 'hovering a toast pauses its countdown');
    assert.ok(source.includes("node.addEventListener('pointerleave', () => resumeToastRemoval(id, node, 'Pointer'))"), 'leaving a toast resumes its countdown');
    assert.ok(source.includes('node.dataset.toastPausePointer || node.dataset.toastPauseFocus'), 'overlapping pointer and keyboard focus do not prematurely resume a toast');
    assert.ok(source.includes('function sessionNotificationScope(session)'), 'session attention notifications enrich the compact scope through one helper');
    assert.ok(source.includes('sessionTabDescription(session, transcriptMeta.sessions?.[session])'), 'session attention notifications include the same concise description used by tabs');
    assert.equal(source.includes('YOLOmux - ${serverHostname}: ${sessionLabel(session)} ${state.label}'), false, 'attention notifications drop verbose host-prefixed titles');
    assert.equal(source.includes('YOLOmux - ${serverHostname}: ${message}'), false, 'watched-PR browser notifications drop verbose host-prefixed titles');
    assert.ok(source.includes("compactNotificationTitle(sessionLabel(session), 'terminal', {inApp: true})"), 'terminal connection toasts use the shared in-app title path without redundant external app context');
    assert.ok(source.includes("showToast(hostNotificationTitle(t('notify.testToastTitle'), {inApp: true})"), 'the in-page notification test uses the shared context-free toast title path');
    assert.ok(source.includes("sendBrowserNotification(t('notify.testTitle', {host: serverHostname})"), 'the browser-to-OS notification test keeps the branded host title');
    assert.ok(source.includes('function renderBrowserAppIconDataUrl(options = {})'), 'favicon and OS notifications share one app-icon renderer');
    assert.ok(source.includes('return renderBrowserAppIconDataUrl({count, showBadge: true})'), 'the favicon enables the activity-count badge through the shared renderer');
    assert.ok(source.includes('renderBrowserAppIconDataUrl({size: 192, showBadge: false})'), 'OS notifications use a badge-free 192px icon through the shared renderer');
    assert.ok(source.includes("new Notification(title, icon ? {icon, ...options} : options)"), 'the shared OS-notification boundary applies the icon to every notification');
    const notificationApi = loadYolomux('', ['1']);
    notificationApi.setTranscriptInfoForTest('1', {
      selected_pane: {current_path: '/home/test/yolomux.dev8001'},
      project: {
        git: {branch: 'keivenchang/DIS-2239__notify-title-desc', root: '/home/test/yolomux.dev8001'},
        pull_request: {number: 10851, title: 'fix: show attention notification description'},
      },
    });
    assert.equal(notificationApi.sessionNotificationTitleForTest('1', {label: 'Needs input'}), 'YOLOmux[1 fix: show attention notification description] Needs input', 'attention notification title includes session number plus tab description');
    assert.equal(notificationApi.sessionNotificationTitleForTest('1', {label: 'Needs input'}, {inApp: true}), 'Needs input', 'in-page attention title omits redundant YOLOmux and session context');
    notificationApi.setAutoApproveStateForTest('1', {agent_windows: [{kind: 'claude', state: 'approval', window_index: 0, window_label: '0:claude', screen_text: 'Do you want to proceed?'}]});
    const attentionState = notificationApi.sessionState('1');
    const attentionLine = notificationApi.attentionToastLineForTest('1', attentionState);
    assert.equal(attentionLine.text, 'Do you want to proceed?', 'the attention toast does not repeat the agent label beside its status button');
    assert.equal(typeof attentionLine.render, 'function', 'the attention toast renders the shared stop/pause-plus-agent status button');
    assert.equal(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))['upload.resultTitle'], '{session} upload', 'upload toast titles omit the redundant YOLOmux host prefix');
    assert.ok(source.includes("localizedHtml('terminal.connection.reconnectingStatus'"), 'terminal reconnect status is i18n-keyed');
    assert.ok(source.includes("t('terminal.connection.reconnectingToast'"), 'terminal reconnect toast is i18n-keyed');
    assert.ok(source.includes("terminalNotConnectedHtml(session)"), 'terminal-not-connected statuses share the localized helper');
    assert.ok(source.includes("t('terminal.connection.connShort'"), 'terminal socket status text is i18n-keyed');
    assert.ok(source.includes("t('terminal.connection.socketsTitle'"), 'terminal socket status title is i18n-keyed');
    assert.ok(source.includes("t('terminal.summary.streamDisconnected')"), 'summary stream disconnect text is i18n-keyed');
    assert.equal(source.includes('Disconnected. Reconnecting in ${'), false, 'terminal reconnect toast does not leak a hardcoded English literal');
    assert.equal(source.includes('terminal is not connected</span>'), false, 'terminal-not-connected status does not leak a hardcoded English literal');
    assert.equal(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))['terminal.connection.reconnectingToast'], 'Disconnected. Reconnecting in {seconds}s.', 'terminal reconnect toast has a source locale key');
    assert.equal(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))['notify.testTitle'], 'YOLOmux[{host}] notifications enabled', 'test notification title uses compact host bracket format');
    assert.equal(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))['notify.testToastTitle'], 'notifications enabled', 'in-page test notification title omits redundant external app context');
    const attentionCss = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/\.panel-toast-stack\s*\{[\s\S]*top:\s*8px[\s\S]*z-index:\s*var\(--z-full-screen-overlay\)/.test(attentionCss), 'pane-local toast stacks render below each pane tab strip and above pane contents');
  });

  test('t@6452', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(source.includes('async function apiFetchJson('), 'D1: shared JSON fetch helper is bundled');
    assert.ok(source.includes('error.status = response.status'), 'D1: JSON fetch errors preserve HTTP status for callers');
    assert.ok(source.includes('error.payload = payload || {}'), 'D1: JSON fetch errors preserve API payloads for callers');
    const jsonFetchFiles = [
      'static_src/js/yolomux/40_file_explorer_files.js',
      'static_src/js/yolomux/70_layout_actions.js',
      'static_src/js/yolomux/78_panel_shell.js',
      'static_src/js/yolomux/80_info_panel.js',
      'static_src/js/yolomux/81_yoagent_panel.js',
      'static_src/js/yolomux/82_preferences_panel.js',
      'static_src/js/yolomux/83_debug_panel.js',
      'static_src/js/yolomux/99_terminal_boot.js',
    ];
    for (const file of jsonFetchFiles) {
      const src = fs.readFileSync(file, 'utf8');
      assert.equal(/const response = await apiFetch\(/.test(src), false, `D1: ${file} should not hand-roll apiFetch response variables`);
      assert.equal(/await response\.json\(\)/.test(src), false, `D1: ${file} should use apiFetchJson instead of manual response.json`);
      assert.equal(/if \(!response\.ok\)/.test(src), false, `D1: ${file} should use apiFetchJson instead of manual response.ok checks`);
    }
    assert.ok((fs.readFileSync('static_src/js/yolomux/40_file_explorer_files.js', 'utf8') + fs.readFileSync('static_src/js/yolomux/45_file_explorer_actions.js', 'utf8')).includes('apiFetch(`/api/fs/unindex'), 'D1: Finder unindex remains fire-and-forget');
    assert.ok(fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8').includes("apiFetch('/api/event'"), 'D1: event telemetry remains fire-and-forget');
  });

  test('t@6473', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    const sourceBlock = (startNeedle, endNeedle = '') => {
      const start = source.indexOf(startNeedle);
      assert.ok(start >= 0, `F1: source block starts with ${startNeedle}`);
      if (!endNeedle) return source.slice(start);
      const end = source.indexOf(endNeedle, start + startNeedle.length);
      assert.ok(end > start, `F1: source block ends with ${endNeedle}`);
      return source.slice(start, end);
    };
    assert.ok(source.includes('const fileState = new Map();'), 'F1: one fileState map owns per-path file/editor state');
    assert.ok(source.includes('const openFiles = fileState;'), 'F1: openFiles is the compatibility alias for fileState');
    for (const obsolete of [
      'const fileEditorTabPaths = new Set()',
      'const filePreviewTabPaths = new Set()',
      'const openFileOwnerSessions = new Map()',
      'const fileEditorViewMode = new Map()',
      'const fileEditorImageMode = new Map()',
      'const editorBlameByPath = new Map()',
      'const fileEditorConflictDialogs = new Set()',
    ]) {
      assert.equal(source.includes(obsolete), false, `F1: removed obsolete path-keyed container ${obsolete}`);
    }
    const setFileStateBlock = sourceBlock('function setFileState(path, state)', 'function deleteFileState(path)');
    assert.ok(/editorTabItems[\s\S]*ownerSessions[\s\S]*viewMode[\s\S]*previewZoom[\s\S]*blame[\s\S]*conflictDialogOpen/.test(setFileStateBlock), 'F1: replacing file content preserves per-path side state on the fileState record');
    const removeOpenFileBlock = sourceBlock('async function removeOpenFile(path, options = {})', 'function closeFileTab(path, options = {})');
    assert.ok(removeOpenFileBlock.includes('deleteFileState(path)'), 'F1: closing the last owner deletes one fileState record');
    const renameOpenFilePathBlock = sourceBlock('function renameOpenFilePath(oldPath, newPath)');
    assert.ok(renameOpenFilePathBlock.includes('deleteFileState(oldPath)') && renameOpenFilePathBlock.includes('setFileState(newPath, state)'), 'F1: rename moves one fileState record');
  });

  test('t@6493', () => {
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/\.actions button,\s*\.info-refresh,\s*\.info-tree-preset,\s*\.btn-base,\s*\.changes-repo-head,[\s\S]*\.file-editor-toolbar button,[\s\S]*display:\s*inline-flex;[\s\S]*align-items:\s*center;[\s\S]*border:\s*0;[\s\S]*background:\s*transparent;[\s\S]*cursor:\s*pointer;[\s\S]*font:\s*inherit;/.test(css), 'I1: common button reset/flex base is centralized');
    assert.equal(/\.actions button\s*\{[^}]*display:\s*inline-flex/.test(css), false, 'I1: topbar actions do not restate the shared inline-flex base');
    assert.equal(/\.info-refresh\s*\{[^}]*cursor:\s*pointer/.test(css), false, 'I1: info refresh does not restate shared cursor behavior');
    assert.equal(/\.file-editor-mode-control button\s*\{[^}]*background:\s*transparent/.test(css), false, 'I1: editor mode buttons do not restate shared transparent background');
  });

  test('t@6501', () => {
    const api = loadYolomux();
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    api.setDocumentTitleNowForTest(0);
    api.updateDocumentTitle();
    assert.equal(api.documentTitleForTest(), 'YOLOmux [idle]');
    api.setDocumentTitleNowForTest(119000);
    api.updateDocumentTitle();
    assert.equal(api.documentTitleForTest(), 'YOLOmux [idle]', 'idle title stays compact before two minutes');
    api.setDocumentTitleNowForTest(121000);
    api.updateDocumentTitle();
    assert.equal(api.documentTitleForTest(), 'YOLOmux (idle for 2 min)', 'idle title shows elapsed minutes after two minutes');
    api.setDocumentTitleNowForTest(181000);
    api.updateDocumentTitle();
    assert.equal(api.documentTitleForTest(), 'YOLOmux (idle for 3 min)', 'idle title minute count advances while idle');
    api.setAutoApproveStateForTest('1', {screen: {key: 'working'}});
    api.setAutoApproveStateForTest('2', {screen: {key: 'idle'}, agent_windows: [{kind: 'claude', state: 'working', window_index: 2, window_label: '2:claude'}]});
    api.setAutoApproveStateForTest('3', {screen: {key: 'idle'}});
    api.updateDocumentTitle();
    assert.equal(api.runningAgentCount(), 2);
    assert.equal(api.documentTitleForTest(), 'YOLOmux [2 running]');
    assert.equal(api.sessionYoloIsWorking('2'), true, 'a hidden/background working window makes the session YO spin');
    api.setAutoApproveStateForTest('1', {screen: {key: 'idle'}});
    api.setAutoApproveStateForTest('2', {screen: {key: 'idle'}});
    api.updateDocumentTitle();
    assert.equal(api.documentTitleForTest(), 'YOLOmux [idle]', 'idle timer resets after a running period');
    assert.ok(/function updateDocumentTitle\(\)[\s\S]*updateBrowserFavicon\(\)/.test(source), 'document title refresh also refreshes the browser favicon badge');
  });

  test('t@6527', () => {
    const api = loadYolomux();
    const info = {
      project: {
        git: {
          branch: 'main',
          root: '/home/test/project',
          upstream: 'origin/main',
          dirty_count: 10,
          behind: 18,
          head: '747c3fd0c6 ci: Update the dep for the whl publish to be automated (#9961)',
          github_repo: {url: 'https://github.com/ai-project/project'},
          other_branches: {
            branches: [
              {
                name: 'main',
                current: true,
                subject: 'ci: Update the dep for the whl publish to be automated (#9961)',
                updated: '13 hours ago',
              },
              {
                name: 'keivenc/DIS-2141__internlm-tool-parser-parity',
                subject: 'feat: add InternLM tool parser parity',
                pull_request: {
                  number: 10075,
                  status_label: 'PASSING',
                  url: 'https://github.com/ai-project/project/pull/10075',
                },
                linear_ids: ['DIS-2141'],
                updated: '3 days ago',
              },
            ],
          },
        },
        pull_request: null,
      },
      selected_pane: {current_path: '/home/test/project'},
    };
    const html = api.tmuxPaneTabHtml('4', info, null, true);
    const tabBadgeSource = fs.readFileSync('static/yolomux.js', 'utf8');
    const tabActivityMarkerHtml = value => value.match(/<span class="session-agent-activity-marker[^"]*">[\s\S]*?<\/span><\/span>/)?.[0] || '';
    assert.ok(tabBadgeSource.includes('function pullRequestNumberIndicatorHtml'), 'tab renders the PR number chip helper');
    assert.ok(/<span class="ci-indicator tab-symbol pr-number-chip pr-status-merged"[^>]*>#9961<\/span>/.test(html), 'merged default-branch tab renders the #number as a purple chip');
    assert.ok(html.includes('>YO<'), 'tab includes YO marker');
    assert.equal(/session-yolo-marker[^"]*tab-symbol/.test(html), false, 'YO marker stays visible when metadata badges are hidden');
    assert.ok(/body\.tab-meta-hidden \.pane-tab \.tab-symbol,\s*body\.tab-meta-hidden \.tmux-pane-tab-token \.tab-symbol\s*\{\s*display:\s*none/.test(fs.readFileSync('static_src/css/yolomux/20_sessions_popovers.css', 'utf8')), 'hidden tab metadata removes tab-symbol chips from both regular and compact tmux tab tokens');
    assert.ok(html.includes('>[4]<'), 'tab includes bracketed session number');
    assert.ok(/\.session-button-identifier\s*\{\s*font-weight:\s*700/.test(fs.readFileSync('static_src/css/yolomux/20_sessions_popovers.css', 'utf8')), 'shared session identifiers render bold across every tab surface');
    assert.ok(html.includes('>MAIN<'), 'tab marks default branch');
    assertNoStandalonePrBadge(html, 'merged default-branch tab');
    const noStatusBallHtml = api.tmuxPaneTabHtml('4', info, {key: 'idle'}, false);
    assert.ok(/session-agent-activity-marker--placeholder[\s\S]*agent-window-activity--status-only[\s\S]*agent-window-status-dot/.test(noStatusBallHtml), 'a tab without a status keeps the invisible canonical ball column');
    assert.equal(noStatusBallHtml.includes('pane-tab-core--without-status-ball'), false, 'all session tabs use one shared status-ball layout path');
    const statusBallHtml = api.tmuxPaneTabHtml('4', info, {key: 'working'}, true, {leadingHtml: '<span class="agent-window-status-dot"></span>'});
    assert.equal(statusBallHtml.includes('pane-tab-core--without-status-ball'), false, 'a tab with a status ball uses the shared status-ball layout path');
    // #42: a source-inferred PR with no explicit status_label still reports no status (we don't trust a
    // raw merged flag on an inferred PR)...
    assert.equal(api.pullRequestStatusLabel({number: 9961, source_only: true, merged: true}), '');
    // ...but an explicit status_label is honored even when source_only, so the default-branch head merge
    // commit (which is, by definition, merged) reports MERGED.
    assert.equal(api.pullRequestStatusLabel({number: 9961, source_only: true, status_label: 'merged'}), 'merged');
    assert.equal(html.includes('MERGED'), false, '#42: a default-branch HEAD merge commit (#9961) consolidates merged state into the purple #number chip');
    assert.ok(html.includes('pr-status-merged'), '#42: the inferred merged PR uses the merged status color on the #number chip');
    assert.equal(html.includes('(#9961)'), false, 'tab title strips duplicated PR suffix');
    api.setActivitySummaryPayloadForTest({
      sessions: {
        '4': {
          local: 'Claude session 4 is idle in project. It currently has 10 files changed. Status check: 10 dirty files; 18 commits behind.',
        },
      },
    });
    const popover = api.sessionPopoverHtml('4', info, 'claude', true);
    assert.ok(/popover-title">tmux session 4 ·/.test(popover), 'session popover title labels the header as a tmux session');
    assert.ok(/popover-subtitle[\s\S]*branch-indicator[^>]*>MAIN<[\s\S]*href="https:\/\/github\.com\/ai-project\/project\/pull\/9961"[\s\S]*pr-number-chip pr-status-merged[^>]*>#9961<[\s\S]*ci: Update the dep/.test(popover), 'merged PR popover header links the purple #number chip using the shared inferred GitHub URL');
    assert.equal(popover.includes('#9961:'), false, 'merged PR popover header omits the old #number text prefix');
    assert.ok(/popover-label">branch<\/div><div class="popover-value"><span class="ci-indicator tab-symbol branch-indicator[^"]*">MAIN<\/span>/.test(popover), 'merged PR popover branch row uses the same MAIN chip as the tab');
    assert.ok(/popover-label">PR<\/div><div class="popover-value"><a href="https:\/\/github\.com\/ai-project\/project\/pull\/9961"[\s\S]*<span class="ci-indicator tab-symbol pr-number-chip pr-status-merged[^"]*">#9961<\/span><\/a>/.test(popover), 'merged PR popover PR row links the same purple #number chip as the header');
    assert.equal(popover.includes('#9961 MERGED'), false, 'merged PR popover omits redundant MERGED text');
    assert.equal(popover.includes('PR #9961'), false, 'merged PR popover avoids repeating PR before the #number value');
    assert.equal(popover.includes('popover-label">desc'), false, 'merged PR popover omits the desc row because the header already carries the PR title');
    assert.equal(popover.includes('Status check:'), false, 'merged PR popover removes the YO!agent status sentence when the dedicated git row is present');
    assert.ok(popover.includes('10 dirty · 18 behind'), 'merged PR popover keeps git facts in the dedicated git row');
    const branchListIndex = popover.indexOf('<div class="branch-list"');
    const detailLabels = [...popover.slice(0, branchListIndex).matchAll(/popover-label">([^<]+)/g)].map(match => match[1]);
    assert.equal(detailLabels[detailLabels.length - 1], 'git', 'merged PR popover makes git the final detail row');
    const headRow = popover.match(/popover-label">HEAD<\/div><div class="popover-value">([^<]+)<\/div><\/div>/)?.[1] || '';
    assert.equal(headRow, '747c3fd0c6', 'merged PR popover HEAD row shows only the SHA, not the repeated subject and PR suffix');
    const branchList = popover.slice(branchListIndex);
    assert.equal(branchList.includes('info-branch-current'), false, 'popover branch list drops the redundant current label');
    assert.ok(/branch-name"><span class="ci-indicator tab-symbol branch-indicator[^"]*">MAIN<\/span>[\s\S]*branch-meta">[\s\S]*pr-number-chip pr-status-merged[^>]*>#9961<\/span>/.test(branchList), 'popover branch list mirrors MAIN and merged PR chips for the current branch');
    assert.equal(branchList.includes('<div class="branch-subject">ci: Update the dep'), false, 'popover branch list suppresses the current branch subject when it duplicates the header title');
    assert.ok(/popover-chip-link[\s\S]*pr-number-chip[^>]*>#10075<\/span>[\s\S]*meta-pr-status pr-status-passing[^>]*>PASSING/.test(branchList), 'popover branch list shows non-current PR numbers as chips while keeping meaningful status text');
    assert.ok(branchList.includes('<div class="branch-subject">feat: add InternLM tool parser parity</div>'), 'popover branch list keeps non-current branch subjects because they add detail');

    const blockedHtml = api.tmuxPaneTabHtml('4', info, {key: 'blocked', short: 'Blocked', label: 'Blocked', reason: 'blocked command'}, false);
    assert.equal(blockedHtml.includes('--attention-animation-delay:'), false, 'generic red attention badges stay static when continuous status pulsing is disabled');

    const genericWorkingHtml = api.tmuxPaneTabHtml('4', info, {key: 'working'}, true);
    assert.equal(/session-yolo-marker[^"]*active[^"]*working/.test(genericWorkingHtml), false, 'generic working state does not pulse YO marker');

    api.setAutoApproveStateForTest('4', {enabled: true, screen: {key: 'working'}});
    const fallbackWorkingHtml = api.tmuxPaneTabHtml('4', info, {key: 'idle'}, true);
    assert.ok(/session-yolo-marker[^"]*active[^"]*working/.test(fallbackWorkingHtml), 'visible screen working falls back to the YO marker when no agent window is attributed');

    api.setAutoApproveStateForTest('4', {enabled: true, screen: {key: 'working'}, agent_windows: [
      {kind: 'claude', state: 'working', window_index: 1, window_label: '1:claude', current: true, window_active: true},
    ]});
    const workingHtml = api.tmuxPaneTabHtml('4', info, {key: 'idle'}, true);
    const workingMarkerHtml = tabActivityMarkerHtml(workingHtml);
    assert.ok(/session-yolo-marker[^"]*active/.test(workingHtml), 'working Claude session tabs keep the YO button visible');
    assert.equal(/session-yolo-marker[^"]*working/.test(workingHtml), false, 'attributed working Claude tabs leave motion to the status ball');
    assert.ok(/session-agent-activity-marker[\s\S]*agent-window-activity--status-only[\s\S]*agent-window-status-dot[\s\S]*status-indicator--working/.test(workingMarkerHtml), 'working Claude session tabs show the shared green status ball');
    assert.equal(workingMarkerHtml.includes('agent-window-status-dot--segmented'), false, 'a single-tone working session tab uses the full-green parent-circle pulse path');
    assert.equal(workingMarkerHtml.includes('agent-icon claude'), false, 'working Claude session tabs omit the Claude icon');
    assert.ok(workingHtml.indexOf('session-yolo-marker') < workingHtml.indexOf('session-agent-activity-marker'), 'YO button stays before the working status ball');

    api.setAutoApproveStateForTest('4', {enabled: true, screen: {key: 'working'}, agent_windows: [
      {kind: 'claude', state: 'idle', window_index: 1, window_label: '1:claude', current: true, window_active: true},
    ]});
    const screenProxyInfo = {panes: [{window: '1', window_name: 'claude', process_label: 'claude', window_active: true, active: true}]};
    const screenProxyModel = api.sessionAgentWindowStatusModelForTest('4', screenProxyInfo);
    const screenProxyWindowBar = api.tmuxWindowBarHtml('4', screenProxyInfo);
    const screenProxyTab = api.tmuxPaneTabHtml('4', screenProxyInfo, api.sessionState('4', screenProxyInfo), true);
    assert.equal(screenProxyModel.screenWorking, true, 'the shared model records the screen working signal once');
    assert.equal(screenProxyModel.agents[0].state, 'working', 'the shared model promotes the current idle row when the screen capture is working');
    assert.equal(api.sessionYoloIsWorking('4'), true, 'session working state consumes the shared model');
    assert.ok(/data-window-index="1"[\s\S]*status-indicator--working/.test(screenProxyWindowBar), 'the window bar consumes the shared screen-proxy row');
    assert.ok(/session-agent-activity-marker[\s\S]*status-indicator--working/.test(screenProxyTab), 'the parent Tab consumes the same shared screen-proxy row');

    api.setAutoApproveStateForTest('4', {enabled: false, screen: {key: 'working'}, agent_windows: [
      {kind: 'claude', state: 'working', window_index: 1, window_label: '1:claude', current: true, window_active: true},
    ]});
    const autoOffWorkingHtml = api.tmuxPaneTabHtml('4', info, {key: 'idle'}, false);
    const autoOffWorkingMarkerHtml = tabActivityMarkerHtml(autoOffWorkingHtml);
    assert.equal(/session-yolo-marker/.test(autoOffWorkingHtml), false, 'auto-off working Claude tabs hide the inactive YO button when there is no prompt');
    assert.ok(/session-agent-activity-marker[\s\S]*agent-window-status-dot[\s\S]*status-indicator--working/.test(autoOffWorkingMarkerHtml), 'working Claude session tabs keep the green status ball even when auto-approve is off');
    assert.equal(autoOffWorkingMarkerHtml.includes('agent-icon claude'), false, 'auto-off working Claude session tabs still omit the Claude icon');

    api.setAutoApproveStateForTest('4', {enabled: false, screen: {key: 'needs-input', text: 'waiting for input', signature: 'ask-4'}});
    const autoOffPromptHtml = api.tmuxPaneTabHtml('4', info, api.sessionState('4', info), false);
    assert.ok(/session-yolo-marker[^"]*inactive/.test(autoOffPromptHtml), 'auto-off prompted tabs offer the inactive YO button');
    assert.ok(/data-auto-session="4"/.test(autoOffPromptHtml), 'auto-off prompted YO button is clickable from the tab');
    const yoloMarkerCss = fs.readFileSync('static/yolomux.css', 'utf8');
    // The working YO marker no longer spins — the glowing green ball beside the agent symbol is the
    // working indicator now. Loading/thinking spinners use the shared status pulse duration instead.
    assert.equal(/\.session-yolo-marker\.working\s*\{[^}]*yolo-marker-rotate/.test(yoloMarkerCss), false, '#23: working YO marker is static (no rotation animation)');
    assert.ok(/\.session-yolo-marker\.yoagent-waiting-spinner[\s\S]*?animation-name:\s*yolo-marker-rotate[\s\S]*?animation-duration:\s*var\(--pulse-duration/.test(yoloMarkerCss), '#23: loading/thinking spinners still spin from the shared status pulse duration');
    assert.equal(yoloMarkerCss.includes('--yolo-working-duration'), false, '#23: the dead --yolo-working-duration token is removed');
    assert.equal(/yolo_rotate_ms|yoloRotationDelay|--yolo-rotation-duration|--yolo-rotate-delay/.test(fs.readFileSync('static/yolomux.js', 'utf8') + yoloMarkerCss), false, '#23: the old Active YO rotation setting and delay variables are removed');
    assert.equal(/\.session-yolo-marker:not\(\.inactive\):not\(\.locked\):not\(\.working\)/.test(yoloMarkerCss), false, '#23: the ambient idle-rotation rule is deleted (idle markers are static)');

    api.setAutoApproveStateForTest('4', {enabled: false, enabled_elsewhere: true, locked: true, lock_owner: {pid: 1234}, screen: {key: 'working'}});
    const externalHtml = api.tmuxPaneTabHtml('4', info, {key: 'idle'}, false);
    assert.ok(/session-yolo-marker[^"]*locked/.test(externalHtml), 'YO owned by another server renders as yellow locked marker');
    assert.equal(/session-yolo-marker[^"]*active/.test(externalHtml), false, 'external YO is not shown as local active YO');
    assert.ok(externalHtml.includes('YOLO on elsewhere'), 'external YO marker title explains ownership is elsewhere');

    api.applyServerMetadataPulsesForTest('4', {main: 20000, pr: 20000});
    const metadataPulseHtml = api.tmuxPaneTabHtml('4', info, {key: 'idle'}, true);
    assert.ok(metadataPulseHtml.includes('branch-indicator metadata-pulse'), 'MAIN badge pulses after metadata change');
    assert.equal(metadataPulseHtml.includes('pr-number-chip metadata-pulse'), false, 'open PR number chip is not pulsed by PR metadata changes');

    const mergedInfo = {
      project: {
        git: {branch: 'feature'},
        pull_request: {number: 12, merged: true, checks: {state: 'success'}},
      },
    };
    api.applyServerMetadataPulsesForTest('8', {status: 20000});
    const mergedPulseHtml = api.tmuxPaneTabHtml('8', mergedInfo, {key: 'idle'}, true);
    assert.ok(mergedPulseHtml.includes('pr-number-chip pr-status-merged metadata-pulse'), 'merged #number chip pulses after status change');

    [
      {session: '9', number: 13, state: 'failure', statusLabel: 'CI failing', statusClass: 'pr-status-failing', pulse: true, label: 'failing open PR'},
      {session: '10', number: 14, state: 'passing', statusLabel: 'open', statusClass: 'pr-status-passing', pulse: true, label: 'passing open PR'},
      {session: '11', number: 15, state: 'pending', statusLabel: 'open', statusClass: 'pr-status-pending', pulse: false, label: 'pending open PR'},
      {session: '12', number: 16, state: 'unknown', statusLabel: 'open', statusClass: '', pulse: false, label: 'unknown open PR'},
    ].forEach(({session, number, state, statusLabel, statusClass, pulse, label}) => {
      if (pulse) api.applyServerMetadataPulsesForTest(session, {ci: 20000});
      const ciHtml = api.tmuxPaneTabHtml(session, {
        project: {
          git: {branch: 'feature'},
          pull_request: {number, status_label: statusLabel, checks: {state}},
        },
      }, {key: 'idle'}, true);
      assertNoStandalonePrBadge(ciHtml, label);
      if (statusClass) assert.ok(ciHtml.includes(statusClass), `${label} renders ${statusClass}`);
      if (pulse) assert.ok(ciHtml.includes('metadata-pulse'), `${label} CI badge is marked after CI change`);
      if (state !== 'unknown') assertSingleCiBadge(ciHtml, label);
    });
    api.setAutoApproveStateForTest('4', {agent_windows: [
      {kind: 'claude', state: 'working', window_index: 0, window_label: '0:claude'},
      {kind: 'codex', state: 'needs-input', window_index: 1, window_label: '1:codex'},
    ]});
    assert.equal(api.sessionState('4', {agents: [{kind: 'claude'}, {kind: 'codex'}], panes: []}).key, 'needs-input', 'a background agent window needing input propagates attention to the session tab');
    api.setAutoApproveStateForTest('4', {agent_windows: [
      {kind: 'codex', state: 'interrupted', window_index: 1, window_label: '1:codex', screen_text: 'What should Codex do instead?'},
    ]});
    assert.equal(api.sessionState('4', {agents: [{kind: 'codex'}], panes: []}).key, 'needs-input', 'an interrupted background agent window propagates attention to the session tab');
    const stoppedAt = Math.floor(Date.now() / 1000) - 5;
    api.setAutoApproveStateForTest('4', {enabled: true, agent_windows: [
      {kind: 'claude', state: 'idle', window_index: 0, window_label: '0:claude', working_stopped_ts: stoppedAt},
      {kind: 'codex', state: 'idle', window_index: 1, window_label: '1:codex', working_stopped_ts: stoppedAt},
    ]});
    const cooldownSessionState = api.sessionState('4', {agents: [{kind: 'claude'}, {kind: 'codex'}], panes: []});
    assert.equal(cooldownSessionState.key, 'cooldown', 'a session whose visible child windows are all cooldown gets a yellow session-level state');
    const cooldownSessionTabHtml = api.tmuxPaneTabHtml('4', {agents: [{kind: 'claude'}, {kind: 'codex'}], panes: []}, cooldownSessionState, true);
    assert.ok(cooldownSessionTabHtml.includes('status-indicator--cooldown'), 'the parent Tab dot uses the shared cooldown tone');
    assert.equal(cooldownSessionTabHtml.includes('session-state-cooldown'), false, 'the parent Tab does not render an empty duplicate cooldown badge');
    api.setAutoApproveStateForTest('4', {enabled: true, agent_windows: [
      {kind: 'claude', state: 'working', window_index: 0, window_label: '0:claude'},
      {kind: 'codex', state: 'idle', window_index: 1, window_label: '1:codex', working_stopped_ts: stoppedAt},
    ]});
    assert.equal(api.sessionState('4', {agents: [{kind: 'claude'}, {kind: 'codex'}], panes: []}).key, 'working', 'a working child outranks cooldown at the session level');
    api.setAutoApproveStateForTest('4', {enabled: true, agent_windows: [
      {kind: 'claude', state: 'needs-input', window_index: 0, window_label: '0:claude'},
      {kind: 'codex', state: 'idle', window_index: 1, window_label: '1:codex', working_stopped_ts: stoppedAt},
    ]});
    assert.equal(api.sessionState('4', {agents: [{kind: 'claude'}, {kind: 'codex'}], panes: []}).key, 'needs-input', 'an attention child outranks cooldown at the session level');
    api.setAutoApproveStateForTest('4', {agent_windows: [
      {kind: 'claude', state: 'working', window_index: 0, window_label: '0:claude'},
      {kind: 'codex', state: 'needs-input', window_index: 1, window_label: '1:codex'},
    ]});
    const agentPopover = api.sessionPopoverHtml('4', {panes: []}, 'claude', false);
    assert.ok(/session-agent-kind[\s\S]*agent-window-status-dot[^"]*status-indicator--working[\s\S]*agent-icon claude[^"]*agent-window-activity-icon--working[\s\S]*0:claude/.test(agentPopover), 'working popover row shows the play glyph before the Claude identity and tmux sub-window label');
    assert.ok(/session-agent-kind[\s\S]*agent-window-status-dot(?=[^"]*status-indicator--attention)[^"]*[\s\S]*agent-icon codex[\s\S]*1:codex/.test(agentPopover), 'attention popover row shows the stop glyph before the Codex identity and tmux sub-window label');
    assert.equal(/agent-window-activity agent-window-activity--attention[^"]*"[^>]*style="[^"]*--attention-animation-delay:[^"]*"[\s\S]*agent-window-status-dot/.test(agentPopover), false, 'a settled red attention marker does not retain a pulse phase after its transition ends');
    assert.equal(/agent-window-status-dot[^>]*style="--attention-animation-delay:/.test(agentPopover), false, 'attention status dot does not carry its own independent animation phase');
    assert.equal(/class="[^"]*session-agent-status[^"]*attention-pulse/.test(agentPopover), false, 'attention popover status text stays static when continuous status pulsing is disabled');
    assert.equal(/class="[^"]*session-agent-status[^"]*" style="--attention-animation-delay:/.test(agentPopover), false, 'attention popover status text does not carry an animation delay');
    assert.equal(agentPopover.includes('[object Object]'), false, 'attention popover status text does not leak option objects into class names');
    assert.ok(agentPopover.includes('&lt;15 sec ago'), 'attention popover status text shows recency instead of approval/needs-input subtype words');
    assert.equal(agentPopover.includes('needs input') || agentPopover.includes('approval'), false, 'attention popover status text drops subtype words');
    assert.ok(agentPopover.includes('tmux sub-window 0:claude'), 'working agent row labels the tmux sub-window explicitly');
    assert.ok(agentPopover.includes('tmux sub-window 1:codex'), 'attention agent row labels the tmux sub-window explicitly');
    assert.equal(agentPopover.includes('tmux sub-window tmux sub-window'), false, 'agent row does not double-label tmux sub-window');
    api.agentWindowActivityIconForTest('codex', 'working', 0, {transitionKey: '4:1::codex', scheduleRefresh: false});
    api.setAutoApproveStateForTest('4', {enabled: true, agent_windows: [
      {kind: 'claude', state: 'needs-input', window_index: 0, window_label: '0:claude'},
      {kind: 'codex', state: 'idle', window_index: 1, window_label: '1:codex', working_stopped_ts: Math.floor(Date.now() / 1000) - 5},
    ]});
    const redTabHtml = api.tmuxPaneTabHtml('4', {panes: []}, null, true);
    const redMarkerHtml = tabActivityMarkerHtml(redTabHtml);
    assert.ok(/session-agent-activity-marker[\s\S]*agent-window-activity--status-only[\s\S]*agent-window-status-dot(?=[^"]*status-indicator--attention)[^"]*agent-window-status-dot--attention-cooldown/.test(redMarkerHtml), 'red+yellow tab status renders one dual red/yellow parent ball');
    assert.equal(redMarkerHtml.includes('agent-icon'), false, 'red+yellow tab status does not render any agent symbol');
    api.agentWindowActivityIconForTest('codex', 'working', 0, {transitionKey: '4:1::codex', scheduleRefresh: false});
    api.setAutoApproveStateForTest('4', {enabled: true, agent_windows: [
      {kind: 'claude', state: 'working', window_index: 0, window_label: '0:claude'},
      {kind: 'codex', state: 'idle', window_index: 1, window_label: '1:codex', working_stopped_ts: Math.floor(Date.now() / 1000) - 5},
    ]});
    const yellowTabHtml = api.tmuxPaneTabHtml('4', {panes: []}, null, true);
    const yellowMarkerHtml = tabActivityMarkerHtml(yellowTabHtml);
    assert.ok(/session-agent-activity-marker[\s\S]*agent-window-activity--status-only[\s\S]*agent-window-status-dot(?=[^"]*status-indicator--working)[^"]*agent-window-status-dot--cooldown-working/.test(yellowMarkerHtml), 'yellow+green tab status renders one dual yellow/green parent ball while the live green child remains the session state');
    assert.equal(yellowMarkerHtml.includes('agent-icon'), false, 'yellow+green tab status does not render any agent symbol');
    api.agentWindowActivityIconForTest('claude', 'working', 0, {transitionKey: '4:2::claude', nowSeconds: 5000, scheduleRefresh: false});
    api.setAutoApproveStateForTest('4', {enabled: true, agent_windows: [
      {kind: 'claude', state: 'working', window_index: 0, window_label: '0:claude'},
      {kind: 'claude', state: 'idle', window_index: 2, window_label: '2:claude'},
    ]});
    const transitionYellowTabHtml = api.tmuxPaneTabHtml('4', {panes: []}, null, true);
    assert.ok(/session-agent-activity-marker[\s\S]*agent-window-status-dot(?=[^"]*status-indicator--working)/.test(transitionYellowTabHtml), 'the active working child remains the session state when another child is in a frontend yellow transition');
    assert.ok(transitionYellowTabHtml.includes('agent-window-status-dot--cooldown-working'), 'a frontend yellow transition plus another working child uses the same dual yellow/green parent ball');
    api.setAutoApproveStateForTest('4', {enabled: true, agent_windows: [
      {kind: 'claude', state: 'needs-input', window_index: 0, window_label: '0:claude'},
      {kind: 'codex', state: 'idle', window_index: 1, window_label: '1:codex', working_stopped_ts: Math.floor(Date.now() / 1000) - 5},
      {kind: 'claude', state: 'working', window_index: 2, window_label: '2:claude'},
    ]});
    const triTabHtml = api.tmuxPaneTabHtml('4', {panes: []}, null, true);
    const triMarkerHtml = tabActivityMarkerHtml(triTabHtml);
    assert.ok(/session-agent-activity-marker[\s\S]*agent-window-activity--status-only[\s\S]*agent-window-status-dot(?=[^"]*status-indicator--attention)[^"]*agent-window-status-dot--attention-cooldown-working/.test(triMarkerHtml), 'red+yellow+green tab status uses all three colors in one parent ball');
    assert.equal(triMarkerHtml.includes('agent-icon'), false, 'red+yellow+green tab status does not render any agent symbol');
    const localeFiles = fs.readdirSync('static_src/locales').filter(name => name.endsWith('.json'));
    for (const file of localeFiles) {
      const catalog = JSON.parse(fs.readFileSync(`static_src/locales/${file}`, 'utf8'));
      assert.ok(catalog['popover.tmuxSession']?.includes('{label}'), `${file} localizes popover.tmuxSession and preserves {label}`);
      assert.ok(catalog['popover.tmuxWindow']?.includes('{label}'), `${file} localizes popover.tmuxWindow and preserves {label}`);
      assert.ok(catalog['popover.sessionId'], `${file} localizes popover.sessionId`);
    }
  });

  test('normal pane tab popover refreshes current agent-window activity', () => {
    const api = loadYolomux('', ['4']);
    api.setTranscriptInfoForTest('4', {panes: []});
    const tab = new TestElement('pane-tab-4');
    tab.classList.add('pane-tab');
    tab.dataset.paneTab = '4';
    const popover = new TestElement('popover-4');
    popover.classList.add('session-popover', 'popover-open');
    popover.setAttribute('role', 'tooltip');
    popover.innerHTML = '<div class="stale">old idle content</div>';
    tab.appendChild(popover);
    api.setDocumentQuerySelectorAllForTest(selector => selector === '.pane-tab[data-pane-tab], .dockview-pane-tab[data-pane-tab]' ? [tab] : []);

    api.setAutoApproveStateForTest('4', {agent_windows: [{kind: 'claude', state: 'working', window_index: 0, window_label: '0:claude'}]});
    api.updateSessionButtonStatesForTest();

    assert.equal(tab.children[0], popover, 'popover element is preserved so existing hover bindings stay valid');
    assert.equal(popover.classList.contains('popover-open'), true, 'open popover state is preserved during refresh');
    assert.equal(popover.getAttribute('role'), 'tooltip', 'popover role is preserved during refresh');
    assert.equal(popover.innerHTML.includes('old idle content'), false, 'normal pane tab popover content is not left stale');
    assert.ok(popover.innerHTML.includes('agent-icon claude') && popover.innerHTML.includes('agent-window-agent-icon--working'), 'normal pane tab popover shows the same flashing Claude state as Tabber');
    api.setDocumentQuerySelectorAllForTest(() => []);
  });

  await testAsync('t@6675', async () => {
    const normalApi = loadYolomux('', ['1', '2']);
    const normalFileMenu = normalApi.appMenuTree().find(menu => menu.id === 'file');
    const normalStatsItem = normalFileMenu.items.find(item => item.label === 'YO!stats');
    assert.ok(normalStatsItem, 'File menu exposes YO!stats without requiring a manual debug=1 URL edit');
    assert.equal(normalStatsItem.checked, false, 'YO!stats is not checked until the debug pane is active');
    assert.equal(normalApi.debugModeEnabledForTest(), false, 'normal pages do not open the YO!stats pane by default');
    normalApi.recordJsDebugEventForTest('api', {method: 'GET', url: '/api/preopen', status: 200, ok: true, durationMs: 7.5});
    assert.equal(normalApi.jsDebugEventsForTest().length, 1, 'YO!stats starts collecting API timing before the pane is opened');
    await normalStatsItem.action();
    assert.equal(normalApi.reloadCountForTest(), 0, 'opening YO!stats from a normal page does not reload');
    assert.equal(parseUrl(normalApi.lastUrlForTest()).get('debug'), null, 'YO!stats menu action does not add debug=1 to the URL');
    assert.equal(normalApi.debugModeEnabledForTest(), true, 'opening YO!stats enables in-page instrumentation');
    assert.equal(normalApi.debugModeExplicitUrlEnabledForTest(), false, 'opening YO!stats from the menu does not count as explicit debug URL mode');
    normalApi.recordClientPerfCounterForTest('focusSet', 0.2, {nodes: 1});
    assert.equal(normalApi.debugPanelHtmlForTest().includes('data-js-debug-client-perf'), false, 'normal YO!stats graph hides raw client-work counters');
    const normalStatsPanes = Object.values(normalApi.serialize(normalApi.currentSlots()).panes);
    assert.ok(normalStatsPanes.some(pane => pane.tabs.includes(normalApi.debugPaneItemId) && pane.active === normalApi.debugPaneItemId), 'YO!stats opens as a normal active layout tab');

    const api = loadYolomux('?debug=1', ['1', '2']);
    assert.equal(api.debugModeEnabledForTest(), true, 'debug=1 still enables instrumentation for legacy diagnostic URLs');
    assert.equal(api.debugModeExplicitUrlEnabledForTest(), true, 'debug=1 is tracked separately from menu-opened YO!stats');
    api.recordClientPerfCounterForTest('focusSet', 0.2, {nodes: 1});
    assert.ok(api.debugPanelHtmlForTest().includes('data-js-debug-client-perf'), 'explicit debug=1 graph shows raw client-work counters');
    assert.equal(api.TAB_TYPES.map(type => type.key).join(','), 'info,yoagent,files,search-history,preferences,debug,image-viewer,file-editor');
    assert.equal(api.resolveLayoutItem('debug'), api.debugPaneItemId, 'debug URL item resolves to the virtual pane');
    assert.equal(api.itemParam(api.debugPaneItemId), 'debug', 'YO!stats pane serializes to the readable debug item');
    const fileMenu = api.appMenuTree().find(menu => menu.id === 'file');
    const statsItem = fileMenu.items.find(item => item.targetItem === api.debugPaneItemId);
    assert.ok(statsItem, 'File menu exposes YO!stats when enabled');
    assert.equal(statsItem.label, 'YO!stats', 'File menu labels the debug stats tab as YO!stats');
    assert.equal(statsItem.checked, false, 'debug=1 alone does not check YO!stats when the stats tab is not in the layout');
    const paletteRows = api.commandPaletteCommandItems().filter(item => item.targetItem === api.debugPaneItemId);
    assert.equal(paletteRows.length, 1, 'command palette lists the Debug pane once through the Tabs group');
    assert.equal(paletteRows[0].label, 'YO!stats', 'command palette labels the debug stats tab as YO!stats');
    api.recordJsDebugEventForTest('api', {method: 'GET', url: '/api/ping', status: 200, ok: true, durationMs: 12.3, requestBytes: 123 * 1024 * 1024, responseBytes: (456 * 1024 * 1024) - 999});
    api.recordJsDebugEventForTest('api', {method: 'GET', url: '/api/activity-summary?locale=en', status: 200, ok: true, durationMs: 4200.4});
    api.recordJsDebugEventForTest('sse', {
      eventType: 'fs_changed',
      trigger: 'watch',
      cache: 'ready',
      computeMs: 22.4,
      receiveLatencyMs: 3.2,
      frameBytes: 999,
      bytes: 900,
      changeSummary: {
        roots_changed: 1,
        entries_added: 2,
        entries_removed: 1,
        entries_modified: 3,
        files_added: 1,
        files_removed: 0,
        files_modified: 2,
        dirs_added: 1,
        dirs_removed: 1,
        dirs_modified: 0,
      },
      listingSummary: {
        roots_listed: 2,
        roots_error: 0,
        entries_listed: 44,
      },
    });
    api.recordJsDebugEventForTest('error', {message: 'boom', source: '/static/yolomux.js', line: 10});
    const graphNow = Date.now();
    api.recordJsDebugStatsSampleForTest({
      time: graphNow / 1000,
      cpu_percent: 7.5,
      system_cpu_percent: 22.5,
      uptime_seconds: 125,
      pid: 4242,
      rss_bytes: 134217728,
      history: {
        sequence: 9,
        records: [{
          start: Math.floor(graphNow / 1000),
          duration: 1,
          sequence: 9,
          api_count: 2,
          sse_count: 1,
          latency_total_ms: 15.5,
          latency_count: 2,
          bandwidth_bytes: 4096,
          cpu_total_percent: 7.5,
          cpu_count: 1,
          system_cpu_total_percent: 22.5,
          system_cpu_count: 1,
        }],
      },
    });
    assert.equal(api.jsDebugEventsForTest().length, 4, 'debug event recorder stores bounded diagnostics while enabled');
    const html = api.debugPanelHtmlForTest();
    assert.ok(html.includes('data-js-debug-subtab="events"') && html.includes('API/SSE'), 'YO!stats renders an API/SSE sub-tab');
    assert.ok(html.includes('data-js-debug-subtab="graph"') && html.includes('Graph'), 'YO!stats renders a Graph sub-tab');
    assert.ok(html.indexOf('data-js-debug-subtab="graph"') < html.indexOf('data-js-debug-subtab="events"'), 'YO!stats puts the Graph button left of API/SSE');
    assert.ok(html.includes('data-js-debug-subview="graph"') && html.includes('data-js-debug-subview="events" hidden'), 'YO!stats defaults to the Graph sub-tab and keeps API/SSE separate');
    assert.ok(html.includes('data-js-debug-log'), 'debug panel renders one copyable text log');
    assert.ok(html.includes('data-js-debug-stat="api">2<'), 'debug panel surfaces the API call count');
    assert.ok(html.includes('data-js-debug-graph'), 'YO!stats graph sub-tab renders a graph container');
    for (const chart of ['count', 'latency', 'bandwidth', 'cpu']) {
      assert.ok(html.includes(`data-js-debug-chart="${chart}"`), `YO!stats graph splits out the ${chart} chart`);
      assert.ok(html.includes(`data-js-debug-axis-max="${chart}"`), `YO!stats graph renders a unit-aware Y axis for ${chart}`);
    }
    assert.ok(html.includes('data-js-debug-x-axis') && html.includes('data-js-debug-x-tick="start"'), 'YO!stats graph renders time ticks on each X axis');
    assert.equal(html.includes('js-debug-graph-scale'), false, 'YO!stats omits the redundant bottom graph scale footer');
    for (const series of ['api', 'sse', 'latency', 'bandwidth', 'cpu', 'systemCpu']) {
      assert.ok(html.includes(`data-js-debug-series="${series}"`), `YO!stats graph renders the ${series} line`);
      assert.ok(html.includes(`data-js-debug-legend="${series}"`), `YO!stats graph renders the ${series} legend entry`);
    }
    for (const scale of ['1', '5', '10', '30']) {
      assert.ok(html.includes(`data-js-debug-scale="${scale}"`), `YO!stats graph renders the ${scale}s aggregate button`);
    }
    for (const range of ['60', '300', '900', '1800', '3600', '7200', '14400', '28800', '57600', '86400']) {
      assert.ok(html.includes(`data-js-debug-range="${range}"`), `YO!stats graph exposes the ${range}s range slider tick`);
    }
    assert.ok(html.includes('data-js-debug-range-slider') && html.includes('data-js-debug-range-label'), 'YO!stats uses a compact range slider instead of a row of range buttons');
    assert.ok(html.includes('step="any"'), 'YO!stats range slider moves smoothly while still snapping to preset ranges on release');
    assert.ok(html.includes('data-js-debug-chart="agentTokens"') && html.includes('data-js-debug-chart-kind="bar"') && html.includes('data-js-debug-chart-bucket-seconds="60"'), 'YO!stats renders Agent tokens/min as fixed one-minute bars');
    assert.ok(html.includes('data-js-debug-uptime="2m 5s"') && html.includes('yolomux.py uptime 2m 5s') && html.includes('PID=4242') && html.includes('rss 128 MiB'), 'YO!stats graph shows yolomux.py uptime and process stats');
    assert.ok(html.includes('total 123/456 MB up/down'), 'YO!stats graph shows cumulative upload/download totals in MB');
    assert.ok(html.includes('GET /api/ping'), 'debug panel renders API timing rows');
    assert.ok(html.includes('Slow API by max latency') && html.includes('GET /api/activity-summary'), 'debug panel summarizes slow API endpoints by path');
    assert.ok(html.includes('Slow SSE server work') && html.includes('Slow SSE receive latency'), 'debug panel summarizes SSE server time and receive latency');
    assert.ok(html.includes('fs=changed=roots:1 +2 -1 ~3 files=+1 ~2 dirs=+1 -1 listed=44/2'), 'debug panel renders fs_changed change counts');
    assert.ok(html.includes('SSE') && html.includes('3.2ms') && html.includes('rx=999B'), 'debug panel renders SSE receive time in the duration column and frame size');
    assert.equal(html.includes('lat=3.2ms'), false, 'debug panel does not use a separate lat= token for SSE rows');
    assert.ok(html.includes('boom'), 'debug panel renders JS error rows');
    const debugPaneSource = fs.readFileSync('static/yolomux.js', 'utf8');
    const debugPaneCss = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(debugPaneSource.includes('const jsDebugStatsPollFastMs = 2000;') && debugPaneSource.includes('const jsDebugStatsPollMs = 30000;') && debugPaneSource.includes('const jsDebugStatsPollTimeoutMs = 5000;') && debugPaneSource.includes('const jsDebugStatsHistoryFlushMs = 30000;') && debugPaneSource.includes('const jsDebugGraphRefreshMs = 30000;'), 'YO!stats retries cold-start samples every two seconds, then keeps its thirty-second steady cadence and a bounded request timeout');
    assert.ok(debugPaneSource.includes("textWithMovingEllipsisHtml(t('debug.waitingForServerStats'))"), 'YO!stats waiting metadata uses the shared localized moving ellipsis');
    assert.ok(/function refreshDebugGraphElement\(graph, \{force = false\} = \{\}\) \{[\s\S]*nowMs - lastRenderedAt < jsDebugGraphRefreshMs/.test(debugPaneSource), 'YO!stats keeps graph geometry stable between scheduled redraws while event counters continue updating');
    assert.ok(/function scheduleJsDebugPanelRefresh\(options = \{\}\) \{[\s\S]*if \(options\.force === true\) jsDebugRenderForce = true;[\s\S]*if \(jsDebugRenderTimer\) return;[\s\S]*const force = jsDebugRenderForce;[\s\S]*jsDebugRenderForce = false;[\s\S]*refreshDebugPanelsFromEvents\(\{force\}\)/.test(debugPaneSource), 'YO!stats latches a forced redraw into an already-pending shared refresh');
    assert.equal((debugPaneSource.match(/scheduleJsDebugPanelRefresh\(\{force: firstSampleApplied\}\);/g) || []).length, 2, 'YO!stats forces the first history or direct CPU sample through the shared refresh path');
    assert.ok(/if \(event\.type === 'pointerdown'\)[\s\S]*jsDebugGraphRangeSliderDragging = true;[\s\S]*return true;[\s\S]*if \(event\.type === 'change'\)[\s\S]*jsDebugGraphRangeSliderDragging = false;[\s\S]*setDebugGraphRangeFromSlider/.test(debugPaneSource), 'YO!stats range dragging preserves the native input and commits only on change');
    assert.ok(/function jsDebugStatsPanelVisible\(\)[\s\S]*debugModeEnabled === true[\s\S]*document\.visibilityState !== 'hidden'[\s\S]*itemIsActivePaneTab\(debugPaneItemId\)/.test(debugPaneSource), 'YO!stats stats polling requires a visible active Debug pane');
    assert.ok(/function applyLayoutSlots\(nextSlots, options = \{\}\)[\s\S]*syncJsDebugStatsPolling\(\{pollNow: true\}\)/.test(debugPaneSource), 'every Chrome-style pane-tab activation re-arms or stops the shared YO!stats sampler');
    assert.ok(/function syncJsDebugStatsPolling\(\{pollNow = true\} = \{\}\)[\s\S]*armJsDebugStatsPolling\(\{pollNow\}\)/.test(debugPaneSource), 'YO!stats uses one polling synchronizer for layout and browser visibility changes');
    assert.ok(/await Promise\.all\(activeSessions\.filter\(isTmuxSession\)[\s\S]*await primeJsDebugStatsBeforeLongLivedStreams\(\)[\s\S]*installClientEventStream\(\)/.test(debugPaneSource), 'YO!stats primes its first sample before the global long-lived SSE streams can consume the remaining HTTP\/1.1 connection slots');
    assert.ok(!/panel\.className = 'panel preferences-panel js-debug-panel'/.test(debugPaneSource), 'Debug panel does not use the Preferences class; Preferences rerenders must not overwrite it');
    assert.ok(/\.preferences-panel,\s*\.js-debug-panel\s*\{[^}]*grid-template-rows:\s*auto auto minmax\(0, 1fr\)/.test(debugPaneCss), 'Debug panel gets the shared panel grid without being a Preferences panel');
    assert.ok(debugPaneCss.includes('.js-debug-subtabs') && debugPaneCss.includes('.js-debug-chart-grid') && debugPaneCss.includes('.js-debug-y-axis') && debugPaneCss.includes('.js-debug-line--cpu') && debugPaneCss.includes('.js-debug-line--systemCpu') && debugPaneCss.includes('.js-debug-area--workingAgents') && debugPaneCss.includes('.js-debug-bar--agentToken') && debugPaneCss.includes('.js-debug-legend'), 'YO!stats ships sub-tab, split chart, Y-axis, area/line/bar graph styling, and legends');
    assert.ok(debugPaneCss.includes('.js-debug-range-slider') && debugPaneCss.includes('.js-debug-hover-line') && debugPaneCss.includes('.js-debug-selection-rect'), 'YO!stats ships compact range slider plus hover and selection overlays');
    assert.ok(/\.js-debug-graph-view\s*\{[\s\S]*--js-debug-api-series:\s*var\(--link-soft\)[\s\S]*--js-debug-sse-series:\s*var\(--accent-gold\)/.test(debugPaneCss), 'YO!stats API/SSE uses separated chart-local series colors');
    assert.ok(/\.js-debug-line--api\s*\{[\s\S]*stroke:\s*var\(--js-debug-api-series\)/.test(debugPaneCss) && /\.js-debug-legend-swatch--api\s*\{[\s\S]*color:\s*var\(--js-debug-api-series\)/.test(debugPaneCss), 'YO!stats API line and legend share the API series color');
    assert.ok(/\.js-debug-line--sse\s*\{[\s\S]*stroke:\s*var\(--js-debug-sse-series\)/.test(debugPaneCss) && /\.js-debug-legend-swatch--sse\s*\{[\s\S]*color:\s*var\(--js-debug-sse-series\)/.test(debugPaneCss), 'YO!stats SSE line and legend share the distinct SSE series color');
    assert.ok(/\.js-debug-line--apiSseTotal\s*\{[\s\S]*stroke:\s*var\(--js-debug-series-color, var\(--js-debug-api-sse-total-series\)\)/.test(debugPaneCss), 'YO!stats combined all-client API+SSE uses one shared distinct series color');
    assert.ok(/\.js-debug-line--cpu\s*\{[\s\S]*stroke:\s*var\(--js-debug-series-color, var\(--active-accent-bright\)\)/.test(debugPaneCss) && /\.js-debug-legend-swatch--cpu\s*\{[\s\S]*color:\s*var\(--js-debug-series-color, var\(--active-accent-bright\)\)/.test(debugPaneCss), 'YO!stats per-server CPU lines and legends consume their shared series color');
    assert.ok(/\.js-debug-line--systemCpu\s*\{[\s\S]*stroke:\s*var\(--bad\)/.test(debugPaneCss) && /\.js-debug-legend-swatch--systemCpu\s*\{[\s\S]*color:\s*var\(--bad\)/.test(debugPaneCss), 'YO!stats system CPU uses the red warning color');
    assert.ok(/\.js-debug-chart\s*\{[\s\S]*border:\s*1px solid color-mix\(in srgb, var\(--line\) 88%, transparent\)[\s\S]*border-radius:\s*6px/.test(debugPaneCss), 'YO!stats encloses each graph in a clear bordered chart box');
    assert.ok(/const jsDebugAgentStatusSeriesKeys = Object\.freeze\(\['askAgents', 'workingAgents', 'transitionAgents', 'idleAgents'\]\)/.test(debugPaneSource) && /const jsDebugAgentStatusLegendSeriesKeys = Object\.freeze\(\['workingAgents', 'askAgents', 'transitionAgents', 'idleAgents'\]\)/.test(debugPaneSource), 'YO!stats agent-status plot and legend order have named series owners');
    assert.ok(/series: jsDebugAgentStatusSeriesKeys/.test(debugPaneSource) && /legendSeries: jsDebugAgentStatusLegendSeriesKeys/.test(debugPaneSource) && /jsDebugAgentStatusBucketValueGetters\[key\]/.test(debugPaneSource), 'YO!stats agent-status chart and bucket values consume the named series owners');
    for (const series of DEBUG_AGENT_STATUS_SERIES) {
      assert.ok(new RegExp(`\\.js-debug-area--${series}\\s*\\{[\\s\\S]*opacity:\\s*0\\.7`).test(debugPaneCss), `YO!stats ${series} stacked area uses a 70% fill`);
    }
    assert.ok(/\.js-debug-bar--agentToken\s*\{[\s\S]*fill:\s*var\(--js-debug-series-color/.test(debugPaneCss), 'YO!stats agent token bars use the per-agent series color');
    assert.equal(debugPaneCss.includes('.js-debug-chart--legend-footer') || debugPaneCss.includes('.js-debug-chart-legend-footer'), false, 'YO!stats has one shared header legend layout for every chart');
    assert.ok(/\.js-debug-y-axis span\s*\{[\s\S]*position:\s*absolute[\s\S]*top:\s*var\(--js-debug-axis-y/.test(debugPaneCss), 'YO!stats Y-axis labels use chart-coordinate positioning');
    assert.ok(/\.js-debug-grid-line\s*\{[\s\S]*stroke-width:\s*0\.4/.test(debugPaneCss), 'YO!stats grid guide lines are very thin');
    assert.ok(/\.js-debug-graph-view\s*\{[\s\S]*--js-debug-idle-agent-status:\s*#3f4754/.test(debugPaneCss), 'YO!stats idle agent status uses a visible dark gray token');
    assert.ok(/body\.theme-light \.js-debug-graph-view\s*\{[\s\S]*--js-debug-idle-agent-status:\s*var\(--editor-line-number\)/.test(debugPaneCss), 'YO!stats idle agent status uses a brighter light-mode gray token');
    assert.ok(/\.js-debug-area--idleAgents\s*\{[\s\S]*fill:\s*var\(--js-debug-idle-agent-status\)/.test(debugPaneCss), 'YO!stats idle stacked area uses the darker idle status fill');
    assert.ok(/\.js-debug-line--idleAgents\s*\{[\s\S]*stroke:\s*var\(--js-debug-idle-agent-status\)/.test(debugPaneCss), 'YO!stats idle line uses the darker idle status stroke');
    assert.ok(/\.js-debug-legend-swatch--idleAgents\s*\{[\s\S]*color:\s*var\(--js-debug-idle-agent-status\)/.test(debugPaneCss), 'YO!stats idle legend uses the darker idle status color');
    assert.ok(/\.js-debug-graph\s*\{[\s\S]*display:\s*flex[\s\S]*flex-direction:\s*column/.test(debugPaneCss) && /\.js-debug-chart-shell\s*\{[\s\S]*flex:\s*1 1 auto/.test(debugPaneCss), 'YO!stats graph keeps header/client-work rows content-height and gives remaining space to the chart shell');
    assert.equal(debugPaneSource.includes('${debugGraphLegendHtml(legendItems)}'), false, 'YO!stats does not append a redundant all-series legend after the chart grid');
    const debugGraphLegendSource = debugPaneSource.slice(debugPaneSource.indexOf('function debugGraphLegendHtml'), debugPaneSource.indexOf('function debugGraphAxisHtml'));
    assert.equal(debugGraphLegendSource.includes('debugGraphValueText'), false, 'YO!stats chart legends show labels only, without current values');
    assert.ok(/\.js-debug-line\s*\{[^}]*stroke-width:\s*1\b/.test(debugPaneCss), 'YO!stats graph solid lines stay thin enough to read overlapping series');
    assert.equal(debugPaneSource.includes("initialSetting('performance.activity_summary_refresh_ms'"), false, 'silent activity-summary polling preference is removed');
    assert.equal(debugPaneSource.includes('activitySummaryBackgroundRefreshMs'), false, 'activity-summary no longer keeps a client background refresh timer');
    assert.ok(debugPaneSource.includes('function activitySummaryIsVisible()'), 'activity-summary visibility tracking remains available for server watch state');
    const debugText = api.jsDebugTextForClipboardForTest();
    assert.ok(debugText.includes('page=/?debug=1'), 'debug text includes the active URL path and query');
    assert.ok(debugText.includes('api=2'), 'debug text exports the API call count');
    assert.ok(debugText.includes(`api_tx=${123 * 1024 * 1024}B`), 'debug text exports API upload byte totals');
    assert.ok(debugText.includes('API') && debugText.includes('GET /api/ping'), 'debug text exports API rows');
    assert.ok(debugText.includes('Slow API by max latency') && debugText.includes('GET /api/activity-summary'), 'debug text exports grouped slow API rows');
    assert.ok(debugText.includes('sse_rx=999B'), 'debug text counts estimated SSE frame bytes');
    assert.ok(debugText.includes('Slow SSE receive latency') && debugText.includes('fs_changed'), 'debug text exports grouped SSE latency rows');
    assert.ok(debugText.includes('fs=changed=roots:1 +2 -1 ~3 files=+1 ~2 dirs=+1 -1 listed=44/2'), 'debug text exports fs_changed change counts');
    assert.ok(debugText.includes('Error') && debugText.includes('boom'), 'debug text exports JS error rows');
    assert.equal(debugText.includes('"events"'), false, 'debug copy payload is compact text, not JSON');
    const url = api.syncInitialLayoutUrlForTest();
    assert.equal(parseUrl(url).get('debug'), '1', 'layout URL updates preserve an explicit legacy debug=1 flag');
    api.recordSseDebugEventForTest('fs_changed', {time: (Date.now() / 1000) + 1000, payload: {trigger: 'watch', cache: 'ready'}}, {data: 'x'});
    assert.equal(api.jsDebugEventsForTest().at(-1).receiveLatencyMs, 0, 'SSE debug receive time clamps tiny client/server clock skew to zero');
    const openedApi = loadYolomux('?debug=1&sessions=debug', ['1']);
    assert.deepStrictEqual(canonical(openedApi.serialize(openedApi.currentSlots()).panes), {
      left: {tabs: [openedApi.debugPaneItemId], active: openedApi.debugPaneItemId},
      slot1: {tabs: [openedApi.fileExplorerItemId], active: openedApi.fileExplorerItemId},
    }, 'debug=1 allows sessions=debug to open the Debug pane directly');
    const injectedApi = loadYolomux('?sessions=files,6,5&layout=row@22(slot2,row@50(left,slot1))&tabs=slot2:files;left:6;slot1:5,info&debug=1', ['5', '6']);
    assert.deepStrictEqual(canonical(injectedApi.serialize(injectedApi.currentSlots()).panes), {
      left: {tabs: ['6'], active: '6'},
      slot1: {tabs: ['5', injectedApi.infoItemId], active: '5'},
      slot2: {tabs: [injectedApi.fileExplorerItemId], active: injectedApi.fileExplorerItemId},
    }, 'debug=1 enables instrumentation without injecting Debug into an existing URL layout');
  });

  test('YO!stats graph retains 24 hours with old timing buckets compressed', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    api.clearJsDebugEventsForTest();
    const now = Date.now();
    for (let i = 0; i < 20; i += 1) {
      api.recordJsDebugEventForTest('api', {
        ts: new Date(now - (2 * 60 * 60 * 1000) + (i * 1000)).toISOString(),
        method: 'GET',
        url: `/api/timing-${i}`,
        status: 200,
        ok: true,
        durationMs: 10 + i,
        requestBytes: 50,
        responseBytes: 150,
      });
    }
    api.recordJsDebugEventForTest('api', {
      ts: new Date(now - (25 * 60 * 60 * 1000)).toISOString(),
      method: 'GET',
      url: '/api/expired',
      status: 200,
      ok: true,
      durationMs: 500,
      requestBytes: 100,
      responseBytes: 100,
    });
    api.recordJsDebugStatsSampleForTest({time: (now - (2 * 60 * 60 * 1000)) / 1000, cpu_percent: 42});
    let summary = api.debugGraphBucketSummaryForTest(now);
    assert.equal(summary.retentionHours, 24, 'YO!stats graph keeps a 24 hour retention window');
    assert.equal(summary.rawWindowSeconds, 3600, 'YO!stats graph keeps high-resolution raw buckets for the last hour');
    assert.equal(summary.rollupBucketSeconds, 30, 'YO!stats graph rolls old samples into thirty-second timing buckets');
    assert.equal(summary.rawBuckets, 0, 'two-hour-old samples are no longer kept as one-second raw buckets');
    assert.ok(summary.rollupBuckets > 0 && summary.rollupBuckets <= 2, 'two-hour-old per-second samples compress into thirty-second buckets');
    assert.equal(summary.scaleSeconds, 5, 'YO!stats graph defaults to five-second aggregate buckets');
    assert.equal(summary.rangeSeconds, 900, 'YO!stats graph defaults to the 15-minute time range');
    assert.equal(summary.displayBuckets, 0, 'two-hour-old timing samples are hidden from the default 15-minute range');
    assert.deepStrictEqual(Array.from(summary.availableRangeSeconds), [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 57600, 86400], 'YO!stats keeps all range slider stops available');
    assert.deepStrictEqual([...summary.series], ['api', 'sse', 'latency', 'bandwidth', ...DEBUG_AGENT_STATUS_SERIES, 'tokensPerAgent', 'systemCpu'], 'graph tracks the fixed API, SSE, latency, bandwidth, agent activity, agent token, and system CPU series while process CPU series are discovered dynamically');
    assert.ok(summary.pendingServerBuckets > 0, 'browser-observed API/SSE graph buckets are queued for server retention');
    api.recordJsDebugStatsSampleForTest({
      uptime_seconds: 3661,
      pid: 4321,
      rss_bytes: 268435456,
      history: {
        sequence: 17,
        records: [{
          start: Math.floor((now - (2 * 60 * 60 * 1000)) / 1000 / 10) * 10,
          duration: 10,
          sequence: 17,
          api_count: 25,
          sse_count: 3,
          latency_total_ms: 250,
          latency_count: 5,
          bandwidth_bytes: 4096,
          cpu_total_percent: 42,
          cpu_count: 1,
          system_cpu_total_percent: 35,
          system_cpu_count: 1,
        }],
      },
    });
    summary = api.debugGraphBucketSummaryForTest(now);
    assert.equal(summary.serverSequence, 17, 'graph applies server-retained history sequence numbers');
    assert.equal(summary.uptimeSeconds, 3661, 'graph remembers server-reported yolomux.py uptime');
    const preRestartHtml = api.debugPanelHtmlForTest();
    assert.ok(preRestartHtml.includes('uptime 1h 1m 1s') && preRestartHtml.includes('PID=4321'), 'graph renders server-retained process metadata');
    api.recordJsDebugStatsSampleForTest({
      uptime_seconds: 1,
      pid: 9876,
      started_at: 123,
      history: {
        sequence: 1,
        records: [],
      },
    });
    summary = api.debugGraphBucketSummaryForTest(now);
    assert.equal(summary.serverSequence, 1, 'graph resets incremental history sequence after yolomux.py restarts with a new PID');
    api.setDebugGraphRangeForTest(7200);
    summary = api.debugGraphBucketSummaryForTest(now);
    assert.equal(summary.rangeSeconds, 7200, 'clickable graph range changes the rendered history window');
    assert.equal(summary.scaleSeconds, 5, 'changing range does not override the selected aggregate bucket size');
    assert.ok(summary.displayBuckets > 0, 'two-hour range displays the retained two-hour bucket');
    api.debugGraphApplyServerHistoryForTest({
      sequence: 18,
      records: [{
        start: Math.floor((now - (9 * 60 * 60 * 1000)) / 1000 / 10) * 10,
        duration: 10,
        sequence: 18,
        api_count: 1,
        latency_total_ms: 10,
        latency_count: 1,
      }],
    });
    summary = api.debugGraphBucketSummaryForTest(now);
    assert.ok(summary.availableRangeSeconds.includes(28800), '8 hour range remains available');
    assert.ok(summary.availableRangeSeconds.includes(57600), '16 hour range remains available');
    assert.ok(summary.availableRangeSeconds.includes(86400), '24 hour range remains available');
    api.debugGraphApplyServerHistoryForTest({
      sequence: 19,
      records: [{
        start: Math.floor((now - (17 * 60 * 60 * 1000)) / 1000 / 10) * 10,
        duration: 10,
        sequence: 19,
        sse_count: 1,
        bandwidth_bytes: 20,
      }],
    });
    summary = api.debugGraphBucketSummaryForTest(now);
    assert.ok(summary.availableRangeSeconds.includes(86400), '24 hour range remains available after retained history grows');
    api.debugGraphApplyServerHistoryForTest({
      sequence: 20,
      records: [{
        start: Math.floor((now - 500) / 1000),
        duration: 1,
        sequence: 20,
        sse_count: 1,
        latency_total_ms: 2.5,
        latency_count: 1,
        bandwidth_bytes: 40,
        clients: {
          'client-alpha': {api_count: 2, sse_count: 1, latency_total_ms: 20, latency_count: 1, bandwidth_bytes: 100},
          'client-beta': {api_count: 3, sse_count: 1, latency_total_ms: 30, latency_count: 1, bandwidth_bytes: 200},
          'client-gamma': {api_count: 4, sse_count: 1, latency_total_ms: 40, latency_count: 1, bandwidth_bytes: 300},
        },
      }],
    });
    summary = api.debugGraphBucketSummaryForTest(now);
    assert.ok(summary.rawBuckets > 0, 'recent server timing samples stay in one-second buckets');
    api.setDebugGraphScaleForTest(10);
    api.setDebugGraphRangeForTest(7200);
    summary = api.debugGraphBucketSummaryForTest(now);
    assert.equal(summary.scaleSeconds, 10, 'selected two-hour graph range keeps the chosen aggregate interval');
    const html = api.debugPanelHtmlForTest();
    assert.equal(html.includes('10s buckets | 2h'), false, 'graph omits the redundant bottom scale footer');
    assert.ok(html.includes('data-js-debug-range="28800"') && html.includes('data-js-debug-range="57600"') && html.includes('data-js-debug-range="86400"'), 'graph renders long range slider stops');
    assert.ok(html.includes('Client API&amp;SSE/sec') && html.includes('Client bandwidth/sec'), 'chart headers carry per-second units');
    assert.ok(/<div class="js-debug-chart-head">\s*<div class="js-debug-chart-heading-row">\s*<span class="js-debug-chart-title">Client latency<\/span>[\s\S]*?<\/div>\s*<div class="js-debug-legend"/.test(html), 'chart title owns a full row above its legend');
    assert.ok(html.includes('API (this client)') && html.includes('Client latency (this client)'), 'solid client series identify the current browser in the legend');
    assert.match(html, /data-js-debug-series="api"[^>]*data-js-debug-client-line="solid"/, 'the current browser uses a solid API line');
    assert.match(html, /data-js-debug-series="client:all-clients-total:apiSseTotal"[^>]*data-js-debug-client-line="dot"/, 'all browsers share one dotted combined API+SSE total line');
    assert.equal(html.includes('client:all-clients-total:api"') || html.includes('client:all-clients-total:sse"'), false, 'separate all-client API and SSE total lines are removed');
    assert.match(html, /data-js-debug-series="client:other-clients-average:latency"[^>]*data-js-debug-client-line="dash"/, 'other browsers share one dashed average latency line');
    assert.match(html, /data-js-debug-series="client:other-clients-average:bandwidth"[^>]*data-js-debug-client-line="dash"/, 'other browsers share one dashed average bandwidth line');
    assert.equal(/data-js-debug-series="client:client-(?:alpha|beta|gamma):/.test(html), false, 'individual peer lines are not rendered');
    assert.ok(/data-js-debug-axis-max="count"[^>]*>[0-9.]+</.test(html), 'count chart Y axis stays terse');
    assert.ok(/data-js-debug-axis-max="latency"[^>]*>[0-9.]+(?:ms|s)</.test(html), 'latency chart Y axis uses compact time units');
    assert.ok(/data-js-debug-axis-max="bandwidth"[^>]*>[0-9.]+(?:B|kB|MB)</.test(html), 'bandwidth chart Y axis uses compact byte labels');
    assert.ok(/data-js-debug-axis-max="cpu"[^>]*>[0-9.]+%</.test(html), 'CPU chart Y axis shows percent units');
    assert.ok(html.includes('system avg CPU %'), 'CPU chart includes system average CPU beside yolomux.py CPU');
    assert.ok(html.includes('uptime 1s') && html.includes('PID=9876') && html.includes('server seq 20'), 'graph renders restarted process metadata with retained sequence');
  });

  test('YO!stats graph uses the selected time range as the X-axis domain', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const now = Date.now();
    api.clearJsDebugEventsForTest();
    api.debugGraphApplyServerHistoryForTest({
      sequence: 25,
      records: [{
        start: Math.floor((now - (60 * 60 * 1000)) / 1000 / 10) * 10,
        duration: 10,
        sequence: 24,
        api_count: 10,
        latency_total_ms: 100,
        latency_count: 1,
      }, {
        start: Math.floor((now - (30 * 60 * 1000)) / 1000 / 10) * 10,
        duration: 10,
        sequence: 25,
        api_count: 20,
        latency_total_ms: 200,
        latency_count: 1,
      }],
    });
    api.setDebugGraphScaleForTest(10);
    api.setDebugGraphRangeForTest(86400);
    const html = api.debugPanelHtmlForTest();
    const match = html.match(/data-js-debug-series="api"[^>]*points="([^"]+)"/);
    assert.ok(match, 'API series renders point coordinates');
    const xValues = match[1].trim().split(/\s+/).map(point => Number(point.split(',')[0]));
    assert.equal(xValues.length, 2, 'sparse history only renders recorded buckets');
    assert.ok(xValues[0] > 550, `one-hour-old data stays near the right edge of the 24h graph, got ${xValues[0]}`);
    assert.ok(xValues[1] > xValues[0] && xValues[1] <= 600, `newer data stays later in the selected 24h graph, got ${xValues.join(',')}`);
    assert.equal(html.includes('10s buckets | 24h'), false, 'graph omits the redundant bottom scale footer');
  });

  test('YO!stats uses server-aggregated token points for wide time ranges', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const now = Date.now();
    api.clearJsDebugEventsForTest();
    api.setDebugGraphRangeForTest(86400);
    api.debugGraphApplyServerHistoryForTest({
      sequence: 40,
      records: [{start: Math.floor(now / 1000), duration: 1, sequence: 40, cpu_total_percent: 1, cpu_count: 1}],
      agent_token_history: {
        sequence: 40,
        resolution_seconds: 300,
        snapshot: true,
        records: Array.from({length: 12}, (_item, index) => ({
          start: Math.floor((now - (60 * 60 * 1000) + index * 300000) / 1000 / 300) * 300,
          duration: 300,
          sequence: 29 + index,
          agent_token_samples: 1,
          agent_token_rates: [{key: '1|0|codex', label: '1:0:codex', total: 10, samples: 1, tokens: 100, seconds: 60, source: 'transcript'}],
        })),
      },
    });
    const summary = api.debugGraphBucketSummaryForTest(now);
    assert.equal(summary.agentTokenResolutionSeconds, 300, '24-hour token history retains the server-selected five-minute resolution');
    assert.equal(summary.agentTokenBuckets, 12, 'wide token history stores only server-aggregated points');
    assert.equal(api.debugGraphAgentTokenDisplayBucketsForTest(now).length, 12, 'Agent tokens/min chart reads the downsampled server point series');
    assert.ok(/data-js-debug-displayed-token-sum="1200"[^>]*>\(sum of tokens from displayed = 1\.2k\)<\/span>/.test(api.debugPanelHtmlForTest()), 'displayed token sum adds all retained token deltas and uses the shared compact formatter');
  });

  test('YO!stats token rates use the sampled elapsed time rather than the selected history bucket width', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const now = Date.now();
    api.clearJsDebugEventsForTest();
    api.setDebugGraphRangeForTest(4 * 60 * 60);
    api.debugGraphApplyServerHistoryForTest({
      sequence: 41,
      records: [{
        start: Math.floor(now / 1000),
        duration: 1,
        sequence: 41,
        cpu_total_percent: 1,
        cpu_count: 1,
      }],
      agent_token_history: {
        sequence: 41,
        resolution_seconds: 120,
        snapshot: true,
        records: [{
          start: Math.floor((now - 120000) / 1000 / 120) * 120,
          duration: 120,
          sequence: 41,
          agent_token_samples: 1,
          agent_token_rates: [{key: '1|0|codex', label: '1:0:codex', total: 100, samples: 1, tokens: 100, seconds: 60, source: 'transcript'}],
        }],
      },
    });
    const html = api.debugPanelHtmlForTest();
    assert.ok(/data-js-debug-axis-max="agentTokens"[^>]*>100</.test(html), '100 tokens over one sampled minute stays 100 tokens/min after the server stores it in a two-minute history bucket');
  });

  test('YO!stats clears incompatible token history when the server schema changes', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const now = Date.now();
    api.clearJsDebugEventsForTest();
    api.debugGraphApplyServerHistoryForTest({
      sequence: 42,
      agent_token_schema_version: 1,
      records: [{
        start: Math.floor(now / 1000),
        duration: 1,
        sequence: 42,
        agent_token_samples: 1,
        agent_token_rates: [{key: 'stale|0|codex', label: 'stale:0:codex', total: 500, samples: 1, tokens: 500, seconds: 60, source: 'transcript'}],
      }],
    });
    assert.ok(api.debugPanelHtmlForTest().includes('stale:0:codex'), 'the initial token schema renders its token series');

    api.debugGraphApplyServerHistoryForTest({sequence: 43, agent_token_schema_version: 2, records: []});

    assert.equal(api.debugGraphBucketSummaryForTest(now).agentTokenSchemaVersion, 2, 'the client tracks the replacement token schema');
    assert.equal(api.debugPanelHtmlForTest().includes('stale:0:codex'), false, 'a schema change removes stale token bars and legend entries');
  });

  test('YO!stats split charts render deterministic Y-axis max labels with units', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const now = Date.now();
    api.clearJsDebugEventsForTest();
    api.debugGraphApplyServerHistoryForTest({
      sequence: 31,
      records: [{
        start: Math.floor((now - 30000) / 1000 / 10) * 10,
        duration: 10,
        sequence: 29,
        api_count: 4,
        sse_count: 2,
        latency_total_ms: 100,
        latency_count: 1,
        bandwidth_bytes: 512,
        cpu_total_percent: 5,
        cpu_count: 1,
        system_cpu_total_percent: 8,
        system_cpu_count: 1,
      }, {
        start: Math.floor((now - 20000) / 1000 / 10) * 10,
        duration: 10,
        sequence: 30,
        api_count: 8,
        sse_count: 4,
        latency_total_ms: 250,
        latency_count: 1,
        bandwidth_bytes: 1024,
        cpu_total_percent: 10,
        cpu_count: 1,
        system_cpu_total_percent: 16,
        system_cpu_count: 1,
      }, {
        start: Math.floor((now - 10000) / 1000 / 10) * 10,
        duration: 10,
        sequence: 31,
        api_count: 20,
        sse_count: 10,
        latency_total_ms: 5000,
        latency_count: 1,
        bandwidth_bytes: 10240,
        cpu_total_percent: 50,
        cpu_count: 1,
        system_cpu_total_percent: 80,
        system_cpu_count: 1,
      }],
    });
    api.setDebugGraphRangeForTest(300);
    const html = api.debugPanelHtmlForTest();

    assert.ok(html.includes('data-js-debug-chart="count"') && html.includes('data-js-debug-chart="latency"') && html.includes('data-js-debug-chart="bandwidth"') && html.includes('data-js-debug-chart="cpu"'), 'YO!stats renders separate charts for unlike units');
    assert.ok(html.includes('Client API&amp;SSE/sec') && html.includes('Client bandwidth/sec'), 'chart titles keep the per-second units');
    assert.ok(/data-js-debug-axis-max="count"[^>]*>2</.test(html), 'count chart Y axis shows compact API/SSE rates');
    assert.ok(/data-js-debug-axis-max="latency"[^>]*>5s</.test(html), 'latency chart Y axis converts large millisecond values to terse seconds');
    assert.ok(/data-js-debug-axis-max="bandwidth"[^>]*>1\.0kB</.test(html), 'bandwidth chart Y axis keeps byte labels terse');
    assert.ok(/data-js-debug-axis-max="cpu"[^>]*>100%</.test(html), 'CPU chart Y axis always uses a 0-100% scale');
    assert.ok(html.includes('yolomux.py CPU %') && html.includes('system avg CPU %'), 'CPU legend shows process and system CPU series together');
    assert.ok(html.includes('data-js-debug-x-tick="start"') && html.includes('data-js-debug-x-tick="mid"') && html.includes('data-js-debug-x-tick="end"'), 'split charts render start/mid/end time ticks on the X axis');
  });

  test('YO!stats rounds graph Y-axis labels to clean unit steps', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const now = Date.now();
    api.clearJsDebugEventsForTest();
    api.debugGraphApplyServerHistoryForTest({
      sequence: 41,
      records: [{
        start: Math.floor((now - 10000) / 1000 / 10) * 10,
        duration: 10,
        sequence: 41,
        api_count: 38,
        sse_count: 17,
        latency_total_ms: 196,
        latency_count: 1,
        bandwidth_bytes: 140 * 1024 * 10,
      }],
    });
    api.setDebugGraphScaleForTest(10);
    const html = api.debugPanelHtmlForTest();

    assert.ok(/data-js-debug-axis-max="count"[^>]*>4</.test(html), 'API/SSE per-second axis rounds 3.8/s to a whole 4');
    assert.ok(/data-js-debug-axis-mid="count"[^>]*>2</.test(html), 'API/SSE per-second midpoint stays whole');
    assert.ok(/data-js-debug-axis-max="latency"[^>]*>200ms</.test(html), 'latency axis rounds 196ms to 200ms');
    assert.ok(/data-js-debug-axis-mid="latency"[^>]*>100ms</.test(html), 'latency midpoint stays readable after rounding');
    assert.ok(/data-js-debug-axis-max="bandwidth"[^>]*>200kB</.test(html), 'bandwidth axis rounds 140kB/s to 200kB');
    assert.ok(/data-js-debug-axis-mid="bandwidth"[^>]*>100kB</.test(html), 'bandwidth midpoint stays readable after rounding');
  });

  await testAsync('YO!stats graph renders one identity line per client metric', async () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const now = Date.now();
    const startSeconds = Math.floor((now - 120000) / 10000) * 10;
    await flushAsyncWork();
    api.clearJsDebugEventsForTest();
    api.debugGraphApplyServerHistoryForTest({
      sequence: 61,
      records: Array.from({length: 12}, (_, i) => {
        const value = i + 1;
        return {
          start: startSeconds + (i * 10),
          duration: 10,
          sequence: 50 + i,
          api_count: value * 10,
          sse_count: (13 - value) * 10,
          latency_total_ms: value * 10,
          latency_count: 1,
          bandwidth_bytes: value * 1024 * 10,
        };
      }),
    });
    api.setDebugGraphScaleForTest(10);
    const html = api.debugPanelHtmlForTest();

    for (const series of ['api', 'sse', 'latency', 'bandwidth', 'cpu', 'systemCpu']) {
      assert.equal(html.includes(`data-js-debug-moving-average="${series}"`), false, `YO!stats renders one ${series} identity line without a same-color moving-average duplicate`);
    }

    const movingAverageValues = api.debugGraphMovingAverageValuesForTest(Array.from({length: 12}, (_, i) => i + 1), 10);
    assert.equal(movingAverageValues.at(-1), 7.5, 'moving average uses the trailing 10 samples for the final value');

    const debugPaneCss = fs.readFileSync('static_src/css/yolomux/30_preferences_changes.css', 'utf8');
    assert.ok(/\.js-debug-line--pattern,[\s\S]*\.js-debug-line--client\s*\{[\s\S]*stroke-dasharray:\s*var\(--js-debug-line-dash, none\)/.test(debugPaneCss), 'client and CPU identities share one line-pattern token');
  });

  test('YO!stats graph combines all-client API and SSE while averaging peer latency and bandwidth', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const now = Date.now();
    const thisClientId = api.jsDebugStatsClientIdForRequestForTest();
    api.clearJsDebugEventsForTest();
    api.debugGraphApplyServerHistoryForTest({
      sequence: 72,
      records: [{
        start: Math.floor((now - 500) / 1000),
        duration: 1,
        sequence: 72,
        api_count: 8,
        sse_count: 4,
        latency_total_ms: 10,
        latency_count: 1,
        bandwidth_bytes: 500,
        clients: {
          [thisClientId]: {api_count: 8, sse_count: 4, latency_total_ms: 10, latency_count: 1, bandwidth_bytes: 500},
          'client-alpha': {api_count: 2, sse_count: 0, latency_total_ms: 20, latency_count: 1, bandwidth_bytes: 100},
          'client-beta': {api_count: 4, sse_count: 2, latency_total_ms: 90, latency_count: 3, bandwidth_bytes: 300},
          'client-gamma': {api_count: 0, sse_count: 0, latency_total_ms: 0, latency_count: 0, bandwidth_bytes: 0},
        },
      }],
    });
    api.setDebugGraphScaleForTest(1);
    const clientSeries = api.debugGraphSeriesDataForTest(now).filter(series => series.clientMetric === true);
    const peerSeries = clientSeries.filter(series => series.clientAggregate === 'otherClientsAverage');
    const totalSeries = clientSeries.filter(series => series.clientAggregate === 'allClientsApiSseTotal');
    assert.equal(peerSeries.length, 2, 'latency and bandwidth each have one peer-average series');
    assert.equal(totalSeries.length, 1, 'API and SSE share one all-client total series');
    assert.equal(clientSeries.filter(series => series.metricKey === 'latency').length, 2, 'latency has only this-client and peer-average series');
    const current = metricKey => clientSeries.find(series => series.metricKey === metricKey && !series.clientAggregate);
    const peers = metricKey => peerSeries.find(series => series.metricKey === metricKey);
    assert.equal(current('latency').values.at(-1), 10, 'current-client latency remains independent');
    assert.equal(peers('latency').values.at(-1), 25, 'peer latency equally averages peer client averages with samples');
    assert.ok(Math.abs(peers('bandwidth').values.at(-1) - (400 / 3)) < 1e-9, 'peer bandwidth average includes zero-valued peer records');
    assert.equal(totalSeries[0].values.at(-1), 20, 'combined API+SSE sums this client and every peer, including zeros');
    assert.equal(totalSeries[0].clientLinePattern, 'dot', 'the combined all-client total uses the dotted line pattern');
    assert.equal(totalSeries[0].color, 'var(--js-debug-api-sse-total-series)', 'the combined total owns a distinct shared color token');
  });

  test('YO!stats graph omits the peer average when no other client exists', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const now = Date.now();
    api.clearJsDebugEventsForTest();
    api.debugGraphApplyServerHistoryForTest({
      sequence: 73,
      records: [{start: Math.floor((now - 500) / 1000), duration: 1, sequence: 73, latency_total_ms: 12, latency_count: 1}],
    });
    const clientSeries = api.debugGraphSeriesDataForTest(now).filter(series => series.clientMetric === true);
    assert.equal(clientSeries.some(series => series.clientAggregate === 'otherClientsAverage'), false, 'empty peer averages are not rendered');
    assert.equal(api.debugPanelHtmlForTest().includes('other clients avg'), false, 'the legend does not advertise an unavailable peer average');
  });

  test('YO!stats graph renders one persisted CPU series per yolomux server', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const now = Date.now();
    api.clearJsDebugEventsForTest();
    api.debugGraphApplyServerHistoryForTest({
      sequence: 74,
      records: [{
        start: Math.floor((now - 500) / 1000),
        duration: 1,
        sequence: 74,
        system_cpu_total_percent: 40,
        system_cpu_count: 1,
        servers: {
          'port:7777': {label: 'yolomux.py :7777', cpu_total_percent: 7, cpu_count: 1},
          'port:8001': {label: 'yolomux.py :8001', cpu_total_percent: 11, cpu_count: 1},
          'port:8002': {label: 'yolomux.py :8002', cpu_total_percent: 22, cpu_count: 1},
          'port:8003': {label: 'yolomux.py :8003', cpu_total_percent: 33, cpu_count: 1},
        },
      }],
    });
    const cpuSeries = api.debugGraphSeriesDataForTest(now).filter(series => series.processCpu === true);
    assert.deepStrictEqual([...cpuSeries.map(series => series.label)], [
      'yolomux.py :7777 CPU %',
      'yolomux.py :8001 CPU %',
      'yolomux.py :8002 CPU %',
      'yolomux.py :8003 CPU %',
    ], 'CPU series are stable and ordered by server label');
    assert.deepStrictEqual([...cpuSeries.map(series => series.values.at(-1))], [7, 11, 22, 33], 'each server keeps its own CPU samples');
    assert.deepStrictEqual([...cpuSeries.map(series => series.linePattern)], ['solid', 'dot', 'dot', 'dot'], 'only the YOLOmux server on the current browser port uses a solid CPU line');
    assert.equal(new Set(cpuSeries.map(series => series.color)).size, 4, 'server CPU lines use distinct colors from the shared palette');
    const html = api.debugPanelHtmlForTest();
    for (const port of ['7777', '8001', '8002', '8003']) {
      assert.ok(html.includes(`data-js-debug-series="cpu:port:${port}"`) && html.includes(`yolomux.py :${port} CPU %`), `CPU chart renders server ${port}`);
    }
    assert.match(html, /data-js-debug-series="cpu:port:7777"[^>]*data-js-debug-line-pattern="solid"/, 'current YOLOmux CPU plot is solid');
    for (const port of ['8001', '8002', '8003']) {
      assert.match(html, new RegExp(`data-js-debug-series="cpu:port:${port}"[^>]*data-js-debug-line-pattern="dot"`), `peer YOLOmux ${port} CPU plot is dotted`);
    }
    assert.match(html, /data-js-debug-series="systemCpu"[^>]*data-js-debug-line-pattern="solid"/, 'system CPU plot is solid');
    assert.ok(html.includes('system avg CPU %'), 'one system CPU series remains beside all process series');
  });

  test('YO!stats graph renders server-shared agent activity and token area charts', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1', '2']);
    const now = Date.now();
    const baselineAt = now - 60000;
    api.clearJsDebugEventsForTest();
    api.debugGraphApplyServerHistoryForTest({
      sequence: 81,
      records: [{
        start: Math.floor(baselineAt / 1000),
        duration: 1,
        sequence: 81,
        ask_agent_total: 2,
        run_agent_total: 0,
        transition_agent_total: 1,
        idle_agent_total: 0,
        active_agent_total: 3,
        inactive_agent_total: 0,
        agent_activity_samples: 1,
      }],
    });
    let baselineSummary = api.debugGraphBucketSummaryForTest(now);
    assert.ok(baselineSummary.charts.includes('activity'), 'agent activity chart appears after the server baseline sample');
    assert.ok(baselineSummary.charts.includes('agentTokens'), 'agent token chart stays visible while waiting for server token-rate records');
    api.debugGraphApplyServerHistoryForTest({
      sequence: 82,
      records: [{
        start: Math.floor(now / 1000),
        duration: 1,
        sequence: 82,
        ask_agent_total: 1,
        run_agent_total: 1,
        transition_agent_total: 0,
        idle_agent_total: 1,
        active_agent_total: 2,
        inactive_agent_total: 1,
        agent_activity_samples: 1,
        tokens_per_agent_total: 70,
        agent_token_samples: 1,
        agent_token_rates: [
          {key: '1|0|claude', label: '1:0:claude', total: 60, samples: 1, tokens: 60, seconds: 60, source: 'transcript'},
          {key: '1|1|codex', label: '1:1:codex', total: 30, samples: 1, tokens: 30, seconds: 60, source: 'transcript'},
          {key: '2|0|codex', label: '2:0:codex', total: 120, samples: 1, tokens: 120, seconds: 60, source: 'transcript'},
        ],
      }],
    });
    let summary = api.debugGraphBucketSummaryForTest(now);
    assert.ok(summary.charts.includes('activity'), 'agent activity chart appears when agent rows exist');
    assert.ok(summary.charts.includes('agentTokens'), 'agent token chart appears when token counters exist');
    assert.deepStrictEqual([...summary.charts], ['latency', 'count', 'bandwidth', 'cpu', 'activity', 'agentTokens'], 'YO!stats charts render in scan order: latency, API/SSE, bandwidth, CPU, agent status, agent tokens');

    const html = api.debugPanelHtmlForTest();
    assert.ok(html.includes('data-js-debug-chart="activity"') && html.includes('Agent status'), 'YO!stats renders the agent status chart');
    assert.ok(html.includes('data-js-debug-chart="agentTokens"') && html.includes('Agent tokens/min'), 'YO!stats renders the optional agent token-rate chart');
    assert.ok(/data-js-debug-displayed-token-sum="210"[^>]*>\(sum of tokens from displayed = 210\)<\/span>/.test(html), 'Agent tokens/min header sums the exact token deltas in displayed buckets');
    assert.ok(/class="js-debug-chart js-debug-chart--token-agents" data-js-debug-chart="agentTokens" data-js-debug-chart-kind="bar" data-js-debug-chart-bucket-seconds="60" data-js-debug-chart-stacked="true"/.test(html), 'agent token chart is marked as stacked one-minute bars');
    const agentTokenChartHtml = html.slice(html.indexOf('data-js-debug-chart="agentTokens"'), html.indexOf('</section>', html.indexOf('data-js-debug-chart="agentTokens"')));
    assert.ok(agentTokenChartHtml.indexOf('js-debug-chart-title') < agentTokenChartHtml.indexOf('data-js-debug-legend="agentToken:'), 'agent token legend renders below the title');
    assert.ok(agentTokenChartHtml.indexOf('data-js-debug-legend="agentToken:') < agentTokenChartHtml.indexOf('js-debug-chart-body'), 'agent token legend uses the shared position above the plot body');
    assert.ok(/data-js-debug-axis-max="activity"[^>]*>3</.test(html), 'agent status Y axis uses the exact stacked attention+working+Transition total');
    for (const value of [3, 2, 1, 0]) {
      assert.ok(html.includes(`data-js-debug-axis-tick="activity" data-js-debug-axis-value="${value}"`), `activity chart Y axis shows whole-number tick ${value}`);
      assert.ok(html.includes(`data-js-debug-grid-line="activity" data-js-debug-grid-value="${value}"`), `activity chart draws a horizontal grid line for whole-number tick ${value}`);
      const axisMatch = html.match(new RegExp(`data-js-debug-axis-value="${value}"[^>]*--js-debug-axis-y: ([0-9.]+)%`));
      const gridMatch = html.match(new RegExp(`data-js-debug-grid-line="activity" data-js-debug-grid-value="${value}"[^>]* y1="([0-9.]+)"`));
      assert.ok(axisMatch && gridMatch, `activity chart exposes comparable axis/grid coordinates for tick ${value}`);
      assert.ok(Math.abs(Number(axisMatch[1]) - ((Number(gridMatch[1]) / 120) * 100)) < 0.05, `activity chart aligns label and grid line for tick ${value}`);
    }
    for (const series of DEBUG_AGENT_STATUS_SERIES) {
      assert.ok(html.includes(`data-js-debug-series="${series}"`), `YO!stats renders the ${series} area outline`);
      assert.ok(html.includes(`data-js-debug-area-series="${series}"`), `YO!stats renders the ${series} filled area`);
      assert.ok(html.includes(`data-js-debug-legend="${series}"`), `YO!stats renders the ${series} legend`);
    }
    const activityChartHtml = html.slice(html.indexOf('data-js-debug-chart="activity"'), html.indexOf('</section>', html.indexOf('data-js-debug-chart="activity"')));
    assert.deepStrictEqual(
      DEBUG_AGENT_STATUS_LEGEND_SERIES.map(series => activityChartHtml.indexOf(`data-js-debug-legend="${series}"`) >= 0 ? series : ''),
      DEBUG_AGENT_STATUS_LEGEND_SERIES,
      'YO!stats Agent status legend includes green, red, yellow, idle entries',
    );
    assert.ok(
      activityChartHtml.indexOf('data-js-debug-legend="workingAgents"') < activityChartHtml.indexOf('data-js-debug-legend="askAgents"')
        && activityChartHtml.indexOf('data-js-debug-legend="askAgents"') < activityChartHtml.indexOf('data-js-debug-legend="transitionAgents"')
        && activityChartHtml.indexOf('data-js-debug-legend="transitionAgents"') < activityChartHtml.indexOf('data-js-debug-legend="idleAgents"'),
      'YO!stats Agent status legend is ordered green, red, yellow, idle',
    );
    assert.ok((html.match(/data-js-debug-token-agent="/g) || []).length >= 3, 'YO!stats renders per-agent generated-token series');
    assert.equal(agentTokenChartHtml.includes('data-js-debug-area-series="agentToken:'), false, 'agent token chart no longer renders token areas');
    assert.ok(agentTokenChartHtml.includes('js-debug-bar--agentToken') && agentTokenChartHtml.includes('data-js-debug-bar-stacked="agentToken:'), 'agent token bars are stacked by agent');
    assert.ok(/data-js-debug-bar-series="agentToken:[^"]+"[\s\S]{0,260}data-js-debug-bar-total="210"/.test(agentTokenChartHtml), 'agent token bar stack top is cumulative across agent token series');
    assert.equal((agentTokenChartHtml.match(/data-js-debug-moving-average="/g) || []).length, 1, 'agent token chart overlays exactly one smoothing line');
    assert.ok(agentTokenChartHtml.includes('data-js-debug-moving-average="agentTokenTotal"') && agentTokenChartHtml.includes('data-js-debug-moving-average-samples="3"'), 'agent token chart smooths the summed token total');
    assert.equal(agentTokenChartHtml.includes('data-js-debug-moving-average="agentToken:'), false, 'agent token chart does not draw a dotted moving average for each agent');
    assert.equal(agentTokenChartHtml.includes('data-js-debug-bar-series="agentTokenTotal"'), false, 'summed token total is an overlay, not another stacked bar');
    assert.ok(/data-js-debug-legend="agentTokenTotal"[\s\S]*<span>All agents total<\/span>/.test(agentTokenChartHtml), 'token legend names the one dotted total average');
    assert.ok(/js-debug-agent-token-cyan:[\s\S]*js-debug-agent-token-orange:[\s\S]*js-debug-agent-token-magenta:[\s\S]*js-debug-agent-token-beige/.test(fs.readFileSync('static_src/css/yolomux/30_preferences_changes.css', 'utf8')), 'agent token bars use a deliberately separated cyan, orange, magenta, and beige palette');
    assert.ok(/\.js-debug-line--agentTokenTotal\.js-debug-line--moving-average\s*\{[\s\S]*stroke-dasharray:\s*1 4/.test(fs.readFileSync('static_src/css/yolomux/30_preferences_changes.css', 'utf8')), 'the total trend uses a stronger dotted stroke');
    assert.ok(/data-js-debug-legend="agentToken:[^"]+"[\s\S]*<span>1:0:claude<\/span>/.test(html), 'per-agent token legend keeps the agent label');
    assert.equal(/data-js-debug-legend="agentToken:[^"]+"[\s\S]{0,240}<strong>/.test(html), false, 'per-agent token legend omits current token/min values');
    assert.ok(/data-js-debug-area-series="transitionAgents"[\s\S]{0,180}data-js-debug-area-stacked="transitionAgents"[\s\S]{0,180}data-js-debug-area-total="2"/.test(html), 'transition area is only yellow transition, not idle agents');
    assert.ok(/data-js-debug-area-series="idleAgents"[\s\S]{0,180}data-js-debug-area-stacked="idleAgents"[\s\S]{0,180}data-js-debug-area-total="3"/.test(html), 'idle area stacks above active status so the top remains the total agent count');
    assert.ok(/data-js-debug-legend="askAgents"[\s\S]*<span>Attention<\/span>/.test(html), 'prompted agents render an attention legend label');
    assert.ok(/data-js-debug-legend="workingAgents"[\s\S]*<span>Working<\/span>/.test(html), 'working agents render a working legend label');
    assert.ok(/data-js-debug-legend="transitionAgents"[\s\S]*<span>Transition<\/span>/.test(html), 'yellow stopped agents render a Transition legend label');
    assert.ok(/data-js-debug-legend="idleAgents"[\s\S]*<span>Idle<\/span>/.test(html), 'idle agents render a grey idle legend label');
    assert.equal(/data-js-debug-legend="workingAgents"[\s\S]{0,180}<strong>/.test(html), false, 'activity legends omit current counts');

    const noTokenApi = loadYolomux('?debug=1&sessions=debug', ['1']);
    noTokenApi.clearJsDebugEventsForTest();
    noTokenApi.debugGraphApplyServerHistoryForTest({
      sequence: 83,
      records: [{
        start: Math.floor(now / 1000),
        duration: 1,
        sequence: 83,
        ask_agent_total: 0,
        run_agent_total: 1,
        transition_agent_total: 0,
        idle_agent_total: 1,
        active_agent_total: 1,
        inactive_agent_total: 1,
        agent_activity_samples: 1,
      }],
    });
    summary = noTokenApi.debugGraphBucketSummaryForTest(now);
    assert.ok(summary.charts.includes('activity'), 'activity chart is independent from token data');
    assert.ok(summary.charts.includes('agentTokens'), 'agent token chart stays visible without token counters');
    const noTokenHtml = noTokenApi.debugPanelHtmlForTest();
    assert.ok(noTokenHtml.includes('data-js-debug-chart="activity"'), 'activity chart renders without token counters');
    assert.ok(noTokenHtml.includes('data-js-debug-chart="agentTokens"'), 'token chart renders without numeric token counters');
    assert.ok(/data-js-debug-displayed-token-sum="0"[^>]*>\(sum of tokens from displayed = 0\)<\/span>/.test(noTokenHtml), 'empty Agent tokens/min charts report a zero displayed sum');
  });

  test('YO!stats aligns server activity and latency samples by timestamp', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const now = Date.now();
    const sampleStart = Math.floor((now - 30000) / 1000 / 10) * 10;
    api.clearJsDebugEventsForTest();
    api.debugGraphApplyServerHistoryForTest({
      sequence: 84,
      records: [{
        start: sampleStart,
        duration: 10,
        sequence: 84,
        latency_total_ms: 200,
        latency_count: 1,
        ask_agent_total: 0,
        run_agent_total: 1,
        transition_agent_total: 0,
        idle_agent_total: 1,
        active_agent_total: 1,
        inactive_agent_total: 1,
        agent_activity_samples: 1,
      }],
    });
    api.setDebugGraphScaleForTest(10);
    const html = api.debugPanelHtmlForTest();
    const latencyMatch = html.match(/data-js-debug-series="latency"[^>]*points="([^"]+)"/);
    const activeMatch = html.match(/data-js-debug-series="workingAgents"[^>]*points="([^"]+)"/);
    assert.ok(latencyMatch && activeMatch, 'latency and activity series both render from server history');
    const latencyX = Number(latencyMatch[1].trim().split(/\s+/)[0].split(',')[0]);
    const activeX = Number(activeMatch[1].trim().split(/\s+/)[0].split(',')[0]);
    assert.equal(activeX, latencyX, 'server activity and latency samples with the same bucket timestamp line up on the X axis');
  });

  test('YO!stats graph controls apply on pointer down before refresh can replace buttons', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const now = Date.now();
    api.clearJsDebugEventsForTest();
    api.debugGraphApplyServerHistoryForTest({
      sequence: 71,
      records: [{
        start: Math.floor((now - (2 * 60 * 60 * 1000)) / 1000 / 10) * 10,
        duration: 10,
        sequence: 71,
        api_count: 1,
      }],
    });
    const panel = new TestElement('debug-panel');
    const scale = new TestElement('graph-scale', 'button');
    const range = new TestElement('graph-range', 'button');
    const slider = new TestElement('graph-range-slider', 'input');
    scale.dataset.jsDebugScale = '10';
    range.dataset.jsDebugRange = '7200';
    slider.dataset.jsDebugRangeSlider = '';
    slider.value = '7';
    panel.appendChild(scale);
    panel.appendChild(range);
    panel.appendChild(slider);
    api.bindDebugPanelForTest(panel);
    const pointerdown = panel.listeners.get('pointerdown')[0];
    const input = panel.listeners.get('input')[0];
    const change = panel.listeners.get('change')[0];
    let prevented = 0;

    pointerdown({target: scale, preventDefault() { prevented += 1; }});
    assert.equal(api.debugGraphBucketSummaryForTest().scaleSeconds, 10, 'single pointerdown applies the graph aggregate bucket size immediately');
    pointerdown({target: range, preventDefault() { prevented += 1; }});
    assert.equal(api.debugGraphBucketSummaryForTest().rangeSeconds, 7200, 'single pointerdown applies the graph time range immediately');
    assert.equal(api.debugGraphBucketSummaryForTest().scaleSeconds, 10, 'range pointerdown does not overwrite the selected aggregate bucket size');
    assert.equal(prevented, 2, 'graph controls claim pointerdown before a refresh can remove the clicked button');
    pointerdown({type: 'pointerdown', target: slider, preventDefault() { prevented += 1; }});
    assert.equal(api.debugGraphBucketSummaryForTest().rangeSeconds, 7200, 'range slider pointerdown leaves native dragging to the browser');
    assert.equal(prevented, 2, 'range slider pointerdown is not claimed before native input can fire');
    slider.value = '7.4';
    input({type: 'input', target: slider, preventDefault() {}});
    assert.equal(api.debugGraphBucketSummaryForTest().rangeSeconds, 28800, 'range slider input updates state without claiming native dragging');
    assert.equal(slider.value, '7.4', 'range slider input keeps fractional thumb position while dragging');
    change({type: 'change', target: slider, preventDefault() {}});
    assert.equal(api.debugGraphBucketSummaryForTest().rangeSeconds, 28800, 'range slider change commits the matching range stop after dragging');
    assert.equal(slider.value, '7', 'range slider change snaps the thumb to the nearest preset stop');
  });

  test('YO!stats hover guides sync across charts and drag-select zooms with reset', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const now = Date.now();
    api.clearJsDebugEventsForTest();
    api.debugGraphApplyServerHistoryForTest({
      sequence: 72,
      records: [{
        start: Math.floor((now - 60000) / 1000),
        duration: 1,
        sequence: 72,
        api_count: 1,
      }],
    });
    api.setDebugGraphRangeForTest(300);
    const panel = new TestElement('debug-panel');
    const graph = new TestElement('graph');
    graph.dataset.jsDebugGraph = '';
    graph.className = 'js-debug-graph';
    const grid = new TestElement('grid');
    grid.dataset.jsDebugChartGrid = '';
    grid.dataset.jsDebugDomainStart = String(now - 300000);
    grid.dataset.jsDebugDomainEnd = String(now);
    const svgA = new TestElement('svg-a', 'svg');
    svgA.className = 'js-debug-line-chart';
    svgA.rect = {left: 100, top: 0, width: 200, height: 120, right: 300, bottom: 120};
    const svgB = new TestElement('svg-b', 'svg');
    svgB.className = 'js-debug-line-chart';
    svgB.rect = {left: 500, top: 0, width: 200, height: 120, right: 700, bottom: 120};
    for (const svg of [svgA, svgB]) {
      const hover = new TestElement(`${svg.id}-hover`, 'line');
      hover.dataset.jsDebugHoverLine = '';
      const selection = new TestElement(`${svg.id}-selection`, 'rect');
      selection.dataset.jsDebugSelectionRect = '';
      svg.appendChild(hover);
      svg.appendChild(selection);
      grid.appendChild(svg);
    }
    graph.appendChild(grid);
    panel.appendChild(graph);
    api.bindDebugPanelForTest(panel);
    const pointerdown = panel.listeners.get('pointerdown')[0];
    const pointermove = panel.listeners.get('pointermove')[0];
    const pointerup = panel.listeners.get('pointerup')[0];

    pointermove({target: svgA, clientX: 150});
    assert.ok(graph.classList.contains('js-debug-graph--hovering'), 'hovering a chart enables the shared hover guide');
    assert.equal(svgA.querySelector('[data-js-debug-hover-line]').getAttribute('x1'), '150.0', 'hover guide uses the hovered chart time ratio');
    assert.equal(svgB.querySelector('[data-js-debug-hover-line]').getAttribute('x1'), '150.0', 'hover guide is synchronized into sibling charts');

    let prevented = 0;
    pointerdown({target: svgA, clientX: 120, button: 0, preventDefault() { prevented += 1; }});
    pointermove({target: svgA, clientX: 220});
    assert.ok(graph.classList.contains('js-debug-graph--selecting'), 'dragging over a chart shows the zoom selection rectangle');
    assert.equal(svgA.querySelector('[data-js-debug-selection-rect]').getAttribute('width'), '300.0', 'selection rectangle spans the dragged time range');
    pointerup({target: svgA, clientX: 220});
    let summary = api.debugGraphBucketSummaryForTest(now);
    assert.ok(prevented === 1 && summary.zoomed, 'drag-select claims the pointer and creates a graph zoom domain');
    assert.ok(summary.zoomRangeSeconds > 140 && summary.zoomRangeSeconds < 160, `zoom range follows selected chart ratio, got ${summary.zoomRangeSeconds}`);

    const reset = new TestElement('graph-reset', 'button');
    reset.dataset.jsDebugZoomReset = '';
    panel.appendChild(reset);
    pointerdown({target: reset, preventDefault() { prevented += 1; }});
    summary = api.debugGraphBucketSummaryForTest(now);
    assert.equal(summary.zoomed, false, 'Reset clears the drag-selected zoom domain');
  });

  await testAsync('YO!stats stats polling does not count itself as API timing', async () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const requests = [];
    await flushAsyncWork();
    assert.equal(api.jsDebugStatsPanelVisibleForTest(), true, 'YO!stats stats polling is enabled when the Debug pane is the active visible tab');
    api.clearJsDebugEventsForTest();
    api.setFetchForTest((url, options = {}) => {
      requests.push({url: String(url), method: String(options.method || 'GET'), body: options.body || ''});
      return Promise.resolve(jsonResponse({ok: true, history: {sequence: 0, records: []}}));
    });

    await api.pollJsDebugStatsSampleForTest();
    api.recordJsDebugEventForTest('api', {method: 'GET', url: '/api/ping', status: 200, ok: true, durationMs: 1});
    const immediateSummary = api.debugGraphBucketSummaryForTest(Date.now());
    assert.ok(immediateSummary.rawBuckets > 0 && immediateSummary.displayBuckets > 0, 'YO!stats renders browser API timing locally before the stats-history round trip');
    const immediateHtml = api.debugPanelHtmlForTest();
    assert.ok(immediateHtml.includes('data-js-debug-chart="count"') && immediateHtml.includes('data-js-debug-chart="latency"'), 'YO!stats immediately shows client API count and latency charts');
    await api.flushJsDebugStatsHistoryForTest();

    const sampleRequest = requests.find(request => request.url.startsWith('/api/stats-sample?'));
    const historyRequest = requests.find(request => request.url === '/api/stats-history');
    assert.ok(sampleRequest, 'YO!stats polling fetches a stats sample');
    assert.ok(historyRequest, 'YO!stats polling flushes browser history through the quiet path');
    const sampleUrl = new URL(sampleRequest.url, 'http://localhost');
    const body = JSON.parse(historyRequest.body);
    assert.equal(sampleRequest.method, 'GET', 'YO!stats sample uses GET');
    assert.equal(historyRequest.method, 'POST', 'YO!stats history uses POST');
    assert.equal(sampleUrl.searchParams.get('since'), '0', 'YO!stats sample keeps incremental since state');
    assert.ok(sampleUrl.searchParams.has('history_start'), `YO!stats initial sample requests a bounded visible-history range: ${sampleRequest.url}`);
    assert.ok(sampleUrl.searchParams.get('client_id'), 'YO!stats sample includes the per-tab client id');
	    assert.equal(sampleUrl.searchParams.get('token_consumer'), '1', 'visible YO!stats polling opts into slower server token scans');
	    assert.equal(body.client_id, sampleUrl.searchParams.get('client_id'), 'YO!stats history posts the same per-tab client id');
	    assert.ok(body.records.some(record => record.api_count === 1), 'YO!stats history posts browser API counters for this client');
	    assert.equal(api.jsDebugEventsForTest().length, 1, 'regular debug event recording remains enabled');
	    const coreUtilsSource = fs.readFileSync('static_src/js/yolomux/10_core_utils.js', 'utf8');
	    const terminalBootSource = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
	    assert.ok(/const apiDebugEnabled = jsDebugCollectionEnabled[\s\S]*const startedAt = apiDebugEnabled \? jsDebugPerformanceNow\(\) : 0[\s\S]*const method = apiDebugEnabled \? jsDebugRequestMethod\(requestOptions\) : ''[\s\S]*const requestBytes = apiDebugEnabled \? jsDebugRequestBytes\(url, requestOptions\) : 0/.test(coreUtilsSource), 'apiFetch skips debug metadata work before fetch when debug collection is disabled');
	    assert.ok(/catch \(error\) \{[\s\S]*if \(apiDebugEnabled\) recordApiDebugEvent\(url, method, startedAt, \{error, requestBytes\}\)[\s\S]*if \(apiDebugEnabled\) \{[\s\S]*recordApiDebugEvent\(url, method, startedAt/.test(coreUtilsSource), 'apiFetch only allocates API debug result payloads when collection is enabled');
	    assert.ok(/function recordSseDebugEvent\(eventType, envelope = \{\}, rawEvent = null\) \{[\s\S]*if \(!jsDebugCollectionEnabled\) return;[\s\S]*const payload = clientEventPayloadFromEnvelope\(envelope\)/.test(terminalBootSource), 'SSE debug recording returns before payload extraction when collection is disabled');
	  });

  await testAsync('YO!stats gates polling but uploads client history outside the active pane', async () => {
    let nextTimerId = 0;
    const timers = [];
    const api = loadYolomux('?debug=1', ['1'], 'http:', 'Linux x86_64', 'admin', {
      setTimeout(callback, ms) {
        const timer = {id: ++nextTimerId, callback, ms};
        timers.push(timer);
        return timer.id;
      },
      clearTimeout() {},
    });
    const requests = [];
    await flushAsyncWork();
    api.setFetchForTest((url, options = {}) => {
      requests.push({url: String(url), method: String(options.method || 'GET'), body: options.body || ''});
      return Promise.resolve(jsonResponse({ok: true, history: {sequence: 0, records: []}}));
    });
    assert.equal(api.debugModeEnabledForTest(), true, 'debug=1 still enables event instrumentation');
    assert.equal(api.jsDebugStatsPanelVisibleForTest(), false, 'stats polling stays off when the Debug tab is not active');

    await api.pollJsDebugStatsSampleForTest();
    const timersBeforeEvent = timers.length;
    api.recordJsDebugEventForTest('api', {method: 'GET', url: '/api/ping', status: 200, ok: true, durationMs: 1});
    const historyTimer = timers.slice(timersBeforeEvent).find(timer => timer.ms === 30000);
    assert.ok(historyTimer, 'inactive instrumentation schedules the normal thirty-second history upload');
    historyTimer.callback();
    await flushAsyncWork();
    await flushAsyncWork();

    assert.equal(requests.some(request => request.url.startsWith('/api/stats-sample?')), false, 'inactive Debug instrumentation does not poll server-global stats');
    const historyRequest = requests.find(request => request.url === '/api/stats-history');
    assert.ok(historyRequest, 'inactive YO!stats still uploads pending client history');
    assert.ok(JSON.parse(historyRequest.body).records.some(record => record.api_count === 1), 'inactive upload retains the recorded API bucket');
    assert.equal(api.jsDebugEventsForTest().length, 1, 'event capture remains available for later YO!stats inspection');
  });

  await testAsync('YO!stats retries timed-out cold starts quickly, then switches to steady polling after its first sample', async () => {
    let nextTimerId = 0;
    const intervals = new Map();
    const activeIntervals = new Set();
    const timeouts = new Map();
    const clearedTimeouts = new Set();
    const api = loadYolomux('?debug=1&sessions=debug', ['1'], 'http:', 'Linux x86_64', 'admin', {
      setInterval(callback, ms) {
        const id = ++nextTimerId;
        intervals.set(id, {callback, ms});
        activeIntervals.add(id);
        return id;
      },
      clearInterval(id) {
        activeIntervals.delete(id);
      },
      setTimeout(callback, ms) {
        const id = ++nextTimerId;
        timeouts.set(id, {callback, ms});
        return id;
      },
      clearTimeout(id) {
        clearedTimeouts.add(id);
      },
    });
    await flushAsyncWork();
    api.stopJsDebugStatsPollingForTest();
    let requests = 0;
    let abortSignal = null;
    api.setFetchForTest((_url, options = {}) => {
      requests += 1;
      if (requests === 1) {
        abortSignal = options.signal;
        return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('stats request timed out')), {once: true}));
      }
      return Promise.resolve(jsonResponse({
        pid: 4242,
        started_at: 1700000000,
        uptime_seconds: 12,
        rss_bytes: 1024,
        cpu_percent: 1,
        history: {sequence: 1, records: []},
      }));
    });

    api.syncJsDebugStatsPollingForTest({pollNow: true});
    const coldInterval = [...activeIntervals].map(id => ({id, ...intervals.get(id)})).at(-1);
    const timeout = [...timeouts.entries()].map(([id, timer]) => ({id, ...timer})).filter(timer => timer.ms === 5000).at(-1);
    assert.equal(coldInterval.ms, 2000, 'cold-start polling uses the two-second retry cadence');
    assert.ok(abortSignal, 'the cold-start stats request receives an abort signal');
    assert.ok(timeout, 'the cold-start stats request arms the five-second timeout');
    timeout.callback();
    await flushAsyncWork();
    await flushAsyncWork();
    assert.equal(abortSignal.aborted, true, 'the timeout aborts a stalled stats request');
    assert.equal(api.jsDebugStatsPollingStateForTest().inFlight, false, 'an aborted stats request releases the in-flight guard for the next fast retry');

    coldInterval.callback();
    await flushAsyncWork();
    await flushAsyncWork();
    const steadyInterval = [...activeIntervals].map(id => ({id, ...intervals.get(id)})).at(-1);
    assert.equal(requests, 2, 'the next cold cadence tick retries the stats request');
    assert.equal(api.jsDebugStatsPollingStateForTest().firstSampleReceived, true, 'a real stats payload records the first successful sample');
    assert.equal(steadyInterval.ms, 30000, 'first success re-arms polling at the unchanged thirty-second steady cadence');
    assert.ok(clearedTimeouts.has(timeout.id), 'the aborted request clears its timeout handle');
    assert.ok([...timeouts.entries()].some(([id, timer]) => timer.ms === 5000 && clearedTimeouts.has(id)), 'the successful request also clears its timeout handle');

    const waitingHtml = api.debugGraphMetaHtmlForTest();
    assert.equal(waitingHtml.includes('Waiting for server stats'), false, 'server metadata replaces the waiting state after a successful sample');
    const waitingApi = loadYolomux('?debug=1&sessions=debug', ['1']);
    const waitingHtmlBeforeSample = waitingApi.debugGraphMetaHtmlForTest();
    assert.ok(waitingHtmlBeforeSample.includes('Waiting for server stats') && waitingHtmlBeforeSample.includes('moving-ellipsis'), 'the localized empty state uses the shared animated dots before the first sample');
    const coldGraphHtml = waitingApi.debugPanelHtmlForTest();
    assert.equal(coldGraphHtml.includes('Waiting for server stats') && coldGraphHtml.includes('No events yet'), false, 'cold YO!stats renders only the waiting metadata, never a stacked second empty state');
    const emptyAfterSampleHtml = api.debugPanelHtmlForTest();
    assert.ok(emptyAfterSampleHtml.includes('No events yet'), 'after the first server sample, an empty selected range keeps its independent graph-body empty state');
  });

  await testAsync('YO!stats records disconnected client gaps with full-height bad-connection overlays', async () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const requests = [];
    await flushAsyncWork();
    api.setFetchForTest((url, options = {}) => {
      requests.push({url: String(url), method: String(options.method || 'GET'), body: options.body || ''});
      return Promise.resolve(jsonResponse({ok: true, history: {sequence: 0, records: []}}));
    });
    const now = Date.now();
    api.recordJsDebugStatsSampleForTest({
      history: {
        sequence: 101,
        records: [{
          start: Math.floor((now - 5000) / 1000),
          duration: 1,
          sequence: 101,
          cpu_total_percent: 0,
          cpu_count: 1,
          system_cpu_total_percent: 0,
          system_cpu_count: 1,
        }],
      },
    });
    api.recordJsDebugDisconnectedSpanForTest(now - 4000, now - 1000);
    const html = api.debugPanelHtmlForTest();

    const disconnectedRangeCount = (html.match(/data-js-debug-disconnected-range="/g) || []).length;
    assert.ok(disconnectedRangeCount >= 3, 'YO!stats renders outage overlays in client communication charts');
    for (const chart of ['latency', 'count', 'bandwidth']) {
      assert.ok(new RegExp(`data-js-debug-chart="${chart}"[\\s\\S]*data-js-debug-disconnected-range=`).test(html), `YO!stats renders the bad-connection block in the ${chart} chart`);
    }
    for (const chart of ['cpu', 'activity', 'agentTokens']) {
      assert.equal(new RegExp(`data-js-debug-chart="${chart}"[\\s\\S]*data-js-debug-disconnected-range=`).test(html), false, `YO!stats does not render bad-connection blocks in server-side ${chart} chart`);
    }
    assert.ok(/class="js-debug-disconnected-range"[^>]* y="0"[^>]* height="120"/.test(html), 'bad-connection overlays cover the full SVG graph area');
    assert.ok(html.includes('Bad connection: no data collected for'), 'bad-connection overlays explain the missing collection interval');
    assert.equal(html.includes('data-js-debug-disconnect-line='), false, 'YO!stats does not render disconnected-client bars in the chart SVG');
    assert.equal(html.includes('class="js-debug-disconnect-line"') || html.includes('Client disconnected'), false, 'disconnected-client spans are not a bottom red baseline overlay');
    assert.ok(api.debugGraphBucketSummaryForTest(now).disconnectedBuckets > 0, 'disconnected spans are kept as graph bucket data');

    await api.flushJsDebugStatsHistoryForTest();
    const historyRequest = requests.find(request => request.url === '/api/stats-history' && request.body.includes('disconnected_ms'));
    assert.ok(historyRequest, 'YO!stats posts disconnected spans to server history');
    const body = JSON.parse(historyRequest.body);
    assert.ok(body.client_id, 'disconnected span history is tied to the per-tab client id');
    assert.ok(body.records.some(record => Number(record.disconnected_ms || 0) > 0), 'disconnected span history includes disconnected_ms');

    const debugPaneCss = fs.readFileSync('static_src/css/yolomux/30_preferences_changes.css', 'utf8');
    assert.ok(/\.js-debug-disconnected-range\s*\{[\s\S]*fill:\s*rgb\(var\(--js-debug-bad-connection-rgb\) \/ 0\.28\)/.test(debugPaneCss), 'bad-connection overlays use subtle translucent red');
    assert.equal(debugPaneCss.includes('.js-debug-disconnect-line'), false, 'disconnected-client baseline CSS is removed');
    const terminalBootSource = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
    assert.ok(terminalBootSource.includes('recordJsDebugClientEventsConnectionState(false)') && terminalBootSource.includes('recordJsDebugClientEventsConnectionState(true)'), 'client-events SSE transitions feed YO!stats disconnected spans');
    const terminalCss = fs.readFileSync('static_src/css/yolomux/50_terminal_file_tree.css', 'utf8');
    assert.ok(/body\.bad-connection \.terminal \.xterm \.xterm-cursor\s*\{[\s\S]*background:\s*var\(--bad\) !important[\s\S]*animation:\s*none !important/.test(terminalCss), 'bad connection forces the terminal cursor to a static red cursor');
    assert.ok(/body\.bad-connection \.terminal \.xterm \.xterm-cursor::before[\s\S]*body\.bad-connection \.terminal \.xterm \.xterm-cursor::after[\s\S]*rotate\(45deg\)[\s\S]*rotate\(-45deg\)/.test(terminalCss), 'bad connection draws an X over the terminal cursor when the xterm cursor DOM supports pseudo-elements');

    const term = {options: {theme: {cursor: '#ffffff', cursorAccent: '#11151d'}, cursorBlink: true}};
    api.registerTerminalForTest('1', term);
    api.setFocusedPanelItem('1');
    api.recordJsDebugClientEventsConnectionStateForTest(false);
    assert.equal(api.testElementForId('body').classList.contains('bad-connection'), true, 'client-events disconnect marks the page as a bad connection');
    assert.equal(term.options.cursorBlink, false, 'bad connection disables terminal cursor blinking');
    assert.equal(term.options.theme.cursor, '#ff6673', 'bad connection uses the red dark-mode cursor override');
    assert.equal(term.options.theme.cursorAccent, '#fff3f4', 'bad connection keeps the red cursor X/text readable');
    api.recordJsDebugClientEventsConnectionStateForTest(true);
    assert.equal(api.testElementForId('body').classList.contains('bad-connection'), false, 'client-events reconnect clears the bad-connection page state');
    assert.equal(term.options.cursorBlink, true, 'reconnect restores terminal cursor blinking');
    assert.equal(term.options.theme.cursor, '#ffea00', 'reconnect restores the focused terminal cursor preference');
  });

  test('YO!stats shades client communication no-data gaps without shading server-side charts', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const now = Date.now();
    const bucketStart = offsetMs => Math.floor(((now - offsetMs) / 1000) / 5) * 5;
    api.setDebugGraphScaleForTest(5);
    api.setDebugGraphRangeForTest(60);
    api.debugGraphApplyServerHistoryForTest({
      sequence: 202,
      records: [
        {
          start: bucketStart(45_000),
          duration: 5,
          sequence: 202,
          api_count: 5,
          sse_count: 2,
          latency_total_ms: 150,
          latency_count: 3,
          bandwidth_bytes: 2048,
          cpu_total_percent: 10,
          cpu_count: 1,
          system_cpu_total_percent: 20,
          system_cpu_count: 1,
        },
        {
          start: bucketStart(25_000),
          duration: 5,
          sequence: 202,
          cpu_total_percent: 11,
          cpu_count: 1,
          system_cpu_total_percent: 21,
          system_cpu_count: 1,
        },
        {
          start: bucketStart(5_000),
          duration: 5,
          sequence: 202,
          api_count: 4,
          sse_count: 1,
          latency_total_ms: 80,
          latency_count: 2,
          bandwidth_bytes: 1024,
          cpu_total_percent: 12,
          cpu_count: 1,
          system_cpu_total_percent: 22,
          system_cpu_count: 1,
        },
      ],
    });
    const html = api.debugPanelHtmlForTest();
    const chartHtml = key => html.match(new RegExp(`<section[^>]*data-js-debug-chart="${key}"[\\s\\S]*?<\\/section>`))?.[0] || '';
    const countChart = chartHtml('count');
    assert.ok((countChart.match(/data-js-debug-no-data-range="/g) || []).length >= 2, 'Client API&SSE chart shades both leading and interior no-data spans');
    assert.ok(/class="js-debug-no-data-range"[^>]* x="0\.0"[^>]* height="120"/.test(countChart), 'leading client no-data block starts at the left edge and covers the graph height');
    assert.ok((countChart.match(/data-js-debug-series="api"/g) || []).length >= 2, 'client API line is split into separate polyline segments instead of drawing a diagonal across the no-data gap');
    for (const chart of ['latency', 'bandwidth']) {
      assert.ok(new RegExp(`data-js-debug-chart="${chart}"[\\s\\S]*data-js-debug-no-data-range=`).test(html), `YO!stats shades no-data spans in client ${chart} chart`);
    }
    for (const chart of ['cpu', 'activity', 'agentTokens']) {
      assert.equal(chartHtml(chart).includes('data-js-debug-no-data-range='), false, `YO!stats does not shade server-side ${chart} chart for client no-data spans`);
    }
    assert.ok(html.includes('No client communication data collected'), 'no-data overlays explain that client communication collection was absent');
    const debugPaneCss = fs.readFileSync('static_src/css/yolomux/30_preferences_changes.css', 'utf8');
    assert.ok(/\.js-debug-no-data-range\s*\{[\s\S]*fill:\s*rgb\(var\(--js-debug-bad-connection-rgb\) \/ 0\.12\)/.test(debugPaneCss), 'generic no-data overlays use a very light red opacity');
  });

  test('session popover lists agent windows with working durations and idle recency', () => {
    const api = loadYolomux('', ['4', '5', '6']);
    const baseInfo = {selected_pane: {current_path: '/repo'}, project: {git: {root: '/repo'}}};
    const noAgentHtml = api.sessionPopoverHtml('4', {...baseInfo, agents: []}, '', false);
    assert.ok(noAgentHtml.includes('no AI agents in this tab'), '0-agent tabs render a clear empty line');

    const now = Date.now() / 1000;
    const multiInfo = {
      ...baseInfo,
      agents: [{kind: 'claude', pane_target: '%10'}, {kind: 'codex', pane_target: '%11'}],
    };
    api.setAutoApproveStateForTest('5', {
      agent_windows: [
        {kind: 'codex', state: 'idle', idle_since: now - 300, last_active_ts: now - 300, window_index: 1, window_name: 'codex', window_label: '1:codex'},
        {kind: 'claude', state: 'working', working_elapsed_seconds: 158, window_index: 0, window_name: 'claude', window_label: '0:claude'},
      ],
    });
    const multiText = api.sessionPopoverHtml('5', multiInfo, 'claude', false).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
    const workingMatch = /0:claude\s+—\s+working for 2m 38s/.exec(multiText);
    const idleMatch = /1:codex(?:\s+○)?\s+—\s+5 min ago/.exec(multiText);
    const workingIndex = workingMatch?.index ?? -1;
    const idleIndex = idleMatch?.index ?? -1;
    assert.ok(workingIndex >= 0, 'working row uses the live status-counter elapsed');
    assert.ok(idleIndex > workingIndex, 'working agents render before idle agents and idle agents use recency text');

    api.setAutoApproveStateForTest('4', {
      agent_windows: [{kind: 'codex', state: 'idle', last_active_ts: now - 5, window_index: 0, window_name: 'codex', window_label: '0:codex'}],
    });
    const recentIdleText = api.sessionPopoverHtml('4', {...baseInfo, agents: [{kind: 'codex'}]}, 'codex', false).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
    assert.ok(recentIdleText.includes('0:codex — &lt;15 sec ago') || recentIdleText.includes('0:codex — <15 sec ago'), 'sub-15-second idle agents use the shared Ago recency label');

    api.setAutoApproveStateForTest('4', {
      agent_windows: [{kind: 'codex', state: 'idle', idle_since: now - 900, last_active_ts: now - 900, window_index: 1, window_name: 'codex', window_label: '1:codex', current: true, window_active: true}],
    });
    const currentIdleHtml = api.sessionPopoverHtml('4', {selected_pane: {current_path: '/repo', window_index: '1'}, project: {git: {root: '/repo'}}, agents: [{kind: 'codex'}]}, 'codex', false);
    const currentIdleText = currentIdleHtml.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
    assert.ok(/1:codex(?:\s+○)?\s+—\s+15 min ago/.test(currentIdleText), 'focused/current idle agent window displays transcript recency, not tmux selection');
    assert.equal(/1:codex(?:\s+○)?\s+—\s+active/.test(currentIdleText), false, 'tmux selection is not treated as recent agent activity');
    assert.ok(/session-agent-row[^"]*state-idle[^"]*current/.test(currentIdleHtml), 'focused/current agent window row carries the current class for header styling');
    assert.equal(/agent-status-active[^"]*status-indicator--active/.test(currentIdleHtml), false, 'focused/current idle agent window does not render an active status pill');
    assert.ok(/agent-icon codex[^"]*agent-window-agent-icon--active/.test(currentIdleHtml), 'focused/current idle agent window renders the moving active Codex glyph');
    assert.equal(/agent-window-status-dot/.test(currentIdleHtml), false, 'focused/current idle agent window does not render a competing status dot');

    const parityInfo = {
      selected_pane: {target: '5:0.0', window: '0', pane: '0', current_path: '/repo/codex-root/src'},
      project: {
        git: {root: '/repo/session-root', branch: 'session-branch'},
        repos: [
          {root: '/repo/codex-root', cwd: '/repo/codex-root/src', branch: 'codex-branch', dirty_count: 4, ahead: 1, primary: true},
          {root: '/repo/claude-root', cwd: '/repo/claude-root/src/deep', branch: 'claude-branch', dirty_count: 0, ahead: 3},
        ],
      },
      agents: [{kind: 'codex', pane_target: '5:0.0'}, {kind: 'claude', pane_target: '5:1.0'}],
      panes: [
        {target: '5:0.0', window: '0', pane: '0', window_active: false, active: true, process_label: 'codex', process_label_pid: 111, command: 'codex', current_path: '/repo/codex-root/src'},
        {target: '5:1.0', window: '1', pane: '0', window_active: true, active: true, process_label: 'claude', process_label_pid: 222, command: 'claude', current_path: '/repo/claude-root/src/deep'},
      ],
      window_metadata: [
        {window: '0', window_index: 0, path: '/repo/codex-root/src', git: {root: '/repo/codex-root', branch: 'codex-branch', dirty_count: 4, ahead: 1}},
        {window: '1', window_index: 1, path: '/repo/claude-root/src/deep', git: {root: '/repo/claude-root', branch: 'claude-branch', dirty_count: 0, ahead: 3}},
      ],
	    };
	    api.setTranscriptInfoForTest('5', parityInfo);
	    api.setFocusedPanelItem('5');
	    api.setFileExplorerModeForTest('tabber');
	    api.setFileExplorerTreeDateModeForTest('relative');
	    api.setTabberSessionFilesForTest('5', [
      {path: 'codex.py', abs_path: '/repo/codex-root/src/codex.py', repo: '/repo/codex-root', status: 'M', mtime: 200, agents: ['codex'], agent_windows: [{kind: 'codex', window: '0', window_index: 0, pane: '0', pane_target: '5:0.0'}]},
      {path: 'claude.py', abs_path: '/repo/claude-root/src/deep/claude.py', repo: '/repo/claude-root', status: 'M', mtime: 300, agents: ['claude'], agent_windows: [{kind: 'claude', window: '1', window_index: 1, pane: '0', pane_target: '5:1.0'}]},
    ]);
    api.setAutoApproveStateForTest('5', {
      agent_windows: [
        {kind: 'codex', state: 'working', working_elapsed_seconds: 65, window_index: 0, window_label: '0:codex', pid: 111, active: false, path_entries: [{path: '/repo/codex-root', mtime: 200, git: {root: '/repo/codex-root', branch: 'codex-branch', dirty_count: 4, ahead: 1}}], git: {root: '/repo/codex-root', branch: 'codex-branch', dirty_count: 4, ahead: 1}},
        {kind: 'claude', state: 'idle', idle_since: now - 3600, last_active_ts: now - 3600, window_index: 1, window_label: '1:claude', pid: 222, active: true, path_entries: [{path: '/repo/claude-root', mtime: 300, git: {root: '/repo/claude-root', branch: 'claude-branch', dirty_count: 0, ahead: 3}}], git: {root: '/repo/claude-root', branch: 'claude-branch', dirty_count: 0, ahead: 3}},
      ],
    });
    const parityPopoverHtml = api.sessionPopoverHtml('5', parityInfo, 'claude', false);
    const parityPopoverText = parityPopoverHtml.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
    assert.ok(/1:claude \(pid=222\)(?:\s+○)?\s+—\s+1 hr ago/.test(parityPopoverText), 'popover keeps the active tmux sub-window row current while showing idle transcript recency');
    assert.ok(parityPopoverText.includes('0:codex (pid=111) — working for 1m 5s'), 'non-focused window keeps its own working state');
    assert.equal((parityPopoverHtml.match(/session-agent-row[^"]*current/g) || []).length, 1, 'popover marks exactly one agent window current');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromHtml(api.tmuxWindowBarHtml('5', parityInfo)), ['1'], 'tmux sub-window bar marks the active tmux sub-window');
    const parityRows = api.tabberRenderedRowsForTest();
    const parityClaudeRow = parityRows.find(row => row.type === 'window' && /^1:claude/.test(row.name));
    const parityCodexRow = parityRows.find(row => row.type === 'window' && /^0:codex/.test(row.name));
    assert.equal(parityClaudeRow?.classes.includes('tabber-active-window'), true, 'Tabber marks the same active tmux sub-window as the popover and window bar');
    assert.equal(parityClaudeRow?.date, '1 hr ago', 'Tabber current window displays idle transcript recency instead of tmux selection as activity');
    assert.ok((parityCodexRow?.nameHtml || '').includes('agent-window-agent-icon--working'), 'Tabber working glyph uses the same working state as the popover');
    const parityTree = api.buildTabberTree();
    const paritySession = parityTree.entries.find(entry => entry.tabber?.session === '5');
    const parityWindows = parityTree.entriesByDir.get('/' + paritySession.name);
    const parityClaudeWindow = parityWindows.find(row => row.tabber.windowIndex === 1);
    const parityClaudeRepos = parityTree.entriesByDir.get('/' + paritySession.name + '/' + parityClaudeWindow.name).map(row => row.tabber.label);
    assert.deepEqual(parityClaudeRepos, ['/repo/claude-root'], 'Tabber and popover share the touched repo root for the active Claude window');
    const parityMetaHtml = api.projectMetaHtml('5', parityInfo);
    assert.ok(parityMetaHtml.includes('/repo/claude-root'), 'Info Bar uses the active AI window touched repo root, not the raw pane cwd subdir');
    assert.ok(parityMetaHtml.includes('claude-branch') && parityMetaHtml.includes('0 dirty') && parityMetaHtml.includes('3 ahead'), 'Info Bar git summary matches the active window metadata');
    assert.equal(parityMetaHtml.includes('/repo/claude-root/src/deep'), false, 'Info Bar does not show the active AI window raw cwd subdir when touched repo metadata exists');
    assert.equal(parityMetaHtml.includes('codex-branch') || parityMetaHtml.includes('4 dirty'), false, 'Info Bar does not leak selected-pane git state for the inactive Codex window');

    api.setAutoApproveStateForTest('6', {
      agent_windows: [{kind: 'codex', state: 'working', working_elapsed_seconds: 3720, window_index: 0, window_name: 'codex', window_label: '0:codex'}],
    });
    const singleText = api.sessionPopoverHtml('6', {...baseInfo, agents: [{kind: 'codex'}]}, 'codex', false).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
    assert.ok(singleText.includes('0:codex — working for 1h 02m'), '1-agent tabs use the shared compact elapsed formatter with the window label');

    const perWindowInfo = {
      selected_pane: {current_path: '/repo/selected-session-path'},
      project: {git: {root: '/repo/session-root', branch: 'session-branch'}},
      agents: [{kind: 'claude'}, {kind: 'codex'}],
      panes: [
        {window: '0', pane: '0', window_active: true, active: true, process_label: 'claude', process_label_pid: 12345, command: 'claude', current_path: '/home/u'},
        {window: '1', pane: '0', window_active: false, active: true, process_label: 'codex', process_label_pid: 24680, command: 'codex', current_path: '/home/u'},
      ],
      window_metadata: [
        {window: '0', window_index: 0, path: '/home/u', git: {root: '/repo/claude', branch: 'claude-branch', dirty_count: 2, head: 'abc1234 claude head'}},
        {window: '1', window_index: 1, path: '/home/u', git: {root: '/repo/codex-a', branch: 'codex-branch', dirty_count: 0, head: 'def5678 codex head'}},
      ],
    };
    api.setTranscriptInfoForTest('5', perWindowInfo);
    api.setTabberSessionFilesForTest('5', [
      {path: 'claude.py', abs_path: '/repo/claude/claude.py', repo: '/repo/claude', status: 'M', mtime: 100, agents: ['claude'], agent_windows: [{kind: 'claude', window: '0', window_index: 0, pane: '0', pane_target: '5:0.0'}]},
      {path: 'codex-a.py', abs_path: '/repo/codex-a/codex-a.py', repo: '/repo/codex-a', status: 'M', mtime: 300, agents: ['codex'], agent_windows: [{kind: 'codex', window: '1', window_index: 1, pane: '0', pane_target: '5:1.0'}]},
      {path: 'codex-b.py', abs_path: '/repo/codex-b/codex-b.py', repo: '/repo/codex-b', status: 'M', mtime: 200, agents: ['codex'], agent_windows: [{kind: 'codex', window: '1', window_index: 1, pane: '0', pane_target: '5:1.0'}]},
    ]);
    api.setAutoApproveStateForTest('5', {
      agent_windows: [
        {kind: 'claude', state: 'working', working_elapsed_seconds: 10, window_index: 0, window_label: '0:claude', pid: 12345, active: true, path_entries: [{path: '/repo/claude', mtime: 100, git: {root: '/repo/claude', branch: 'claude-branch', dirty_count: 2, head: 'abc1234 claude head'}}], git: {root: '/repo/claude', branch: 'claude-branch', dirty_count: 2, head: 'abc1234 claude head'}, transcript: '/logs/claude-session.jsonl', transcript_id: 'claude-session-id'},
        {kind: 'codex', state: 'idle', idle_since: now - 120, last_active_ts: now - 120, window_index: 1, window_label: '1:codex', pid: 24680, active: false, path_entries: [{path: '/repo/codex-a', mtime: 300, git: {root: '/repo/codex-a', branch: 'codex-branch', dirty_count: 0, head: 'def5678 codex head'}}, {path: '/repo/codex-b', mtime: 200, git: {root: '/repo/codex-b', branch: 'codex-b-branch', dirty_count: 0}}], git: {root: '/repo/codex-a', branch: 'codex-branch', dirty_count: 0, head: 'def5678 codex head'}, transcript: '/logs/codex-thread.jsonl', transcript_id: 'codex-thread-id'},
      ],
    });
    const perWindowHtml = api.sessionPopoverHtml('5', perWindowInfo, 'claude', false);
    const perWindowText = perWindowHtml.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
    assert.ok(perWindowText.includes('tmux sub-window 0:claude') && perWindowText.includes('/repo/claude') && perWindowText.includes('claude-branch'), 'popover attributes path and branch to the Claude window');
    assert.ok(perWindowText.includes('tmux sub-window 1:codex') && perWindowText.includes('/repo/codex-a') && perWindowText.includes('/repo/codex-b') && perWindowText.includes('codex-branch'), 'popover attributes touched repo paths and branch to the Codex window');
    assert.equal(perWindowText.includes('/home/u'), false, 'touched repo attribution replaces the bare pane cwd fallback in per-window agent popovers');
    const tabberTree = api.buildTabberTree();
    const sessionFive = tabberTree.entries.find(entry => entry.tabber?.session === '5');
    const tabberCodexWindow = tabberTree.entriesByDir.get('/' + sessionFive.name).find(row => row.tabber.windowIndex === 1);
    const tabberCodexPaths = tabberTree.entriesByDir.get('/' + sessionFive.name + '/' + tabberCodexWindow.name).map(row => row.tabber.label);
    assert.deepEqual(tabberCodexPaths, ['/repo/codex-a', '/repo/codex-b'], 'popover and Tabber share the same per-window touched repo resolver');
    assert.ok(perWindowText.includes('tmux sub-window 0:claude (pid=12345)'), 'popover header shows the Claude PID from the same pane record source as Tabber');
    assert.ok(perWindowText.includes('tmux sub-window 1:codex (pid=24680)'), 'popover header shows the Codex PID from the same pane record source as Tabber');
    assert.ok(perWindowHtml.includes('Session ID') && perWindowHtml.includes('data-copy-path="claude-session-id"') && perWindowHtml.includes('data-copy-path="/logs/claude-session.jsonl"'), 'HT1/HT3: agent popovers show session ID and transcript location with shared copy buttons');
    assert.equal(perWindowHtml.includes('Transcript ID'), false, 'Codex/Claude ID rows are no longer mislabeled as transcript IDs');
    assert.ok(/popover-label">Transcript<\/div><div class="popover-value">[\s\S]*data-copy-path="\/logs\/claude-session\.jsonl"/.test(perWindowHtml), 'the transcript path remains a separate Transcript row');
    assert.ok(perWindowHtml.includes('data-copy-path="codex-thread-id"') && perWindowHtml.includes('data-copy-path="/logs/codex-thread.jsonl"'), 'HT2: transcript rows are attributed per AI window');
    assert.equal(perWindowText.split('tmux sub-window 0:claude').length - 1, 1, 'Claude window label appears once in the merged state/metadata row');
    assert.equal(perWindowText.split('tmux sub-window 1:codex').length - 1, 1, 'Codex window label appears once in the merged state/metadata row');
    assert.equal(perWindowHtml.includes('session-window-metadata-title'), false, 'per-window metadata no longer repeats the tmux sub-window label as a title');
    assert.equal(perWindowText.includes('/repo/selected-session-path'), false, 'multi-agent popover does not render the old selected-pane path as a flat session path');

    api.setTabberSessionFilesForTest('5', []);
    const sharedWindowInfo = {
      ...perWindowInfo,
      window_metadata: [
        {window: '0', window_index: 0, path: '/repo/shared', git: {root: '/repo/shared', branch: 'shared-branch'}},
        {window: '1', window_index: 1, path: '/repo/shared', git: {root: '/repo/shared', branch: 'shared-branch'}},
      ],
    };
    api.setAutoApproveStateForTest('5', {
      agent_windows: [
        {kind: 'claude', state: 'working', working_elapsed_seconds: 10, window_index: 0, window_label: '0:claude', pid: 12345, active: true, path_entries: [{path: '/repo/shared', mtime: 100, git: {root: '/repo/shared', branch: 'shared-branch'}}], git: {root: '/repo/shared', branch: 'shared-branch'}},
        {kind: 'codex', state: 'idle', idle_since: now - 120, last_active_ts: now - 120, window_index: 1, window_label: '1:codex', pid: 24680, active: false, path_entries: [{path: '/repo/shared', mtime: 90, git: {root: '/repo/shared', branch: 'shared-branch'}}], git: {root: '/repo/shared', branch: 'shared-branch'}},
      ],
    });
    const sharedHtml = api.sessionPopoverHtml('5', sharedWindowInfo, 'claude', false);
    const sharedText = sharedHtml.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
    assert.equal(sharedText.split('tmux sub-window 0:claude').length - 1, 1, 'shared-path Claude window label appears once');
    assert.equal(sharedText.split('tmux sub-window 1:codex').length - 1, 1, 'shared-path Codex window label appears once');
    assert.equal(sharedText.split('/repo/shared').length - 1, 1, 'shared metadata collapses to one path block after the window rows');
    assert.equal(sharedHtml.includes('session-window-metadata-title'), false, 'shared metadata no longer renders a duplicate label title');

    const sessionsCss = fs.readFileSync('static_src/css/yolomux/20_sessions_popovers.css', 'utf8');
    assert.ok(/\.session-agent-window-block > \.session-agent-row\s*\{[\s\S]*background:\s*var\(--pane-inactive-tab-bg\)/.test(sessionsCss), 'per-window popover headers use the pane-tab shaded background token');
    assert.ok(/\.session-agent-window-block > \.session-agent-row\.current\s*\{[\s\S]*background:\s*var\(--active-tab-muted-bg\)/.test(sessionsCss), 'current per-window popover header uses the active muted tab background token');
    assert.ok(/\.session-agent-row\s*\{[\s\S]*display:\s*block[\s\S]*overflow-wrap:\s*anywhere[\s\S]*white-space:\s*normal/.test(sessionsCss), 'popover window rows use inline text flow and wrap long labels instead of keeping a fixed left flex column');
  });

  test('t@6754', () => {
    const api = loadYolomux();
    const info = {
      selected_pane: {current_path: '/home/test/project/project3'},
      project: {
        git: {branch: 'keivenc/GH-2132__reasoning-dangling-end-marker', root: '/home/test/project/project3'},
        pull_request: {
          number: 9981,
          title: 'fix(parser): parse dangling reasoning end markers',
          description: 'Parser PR description mentions fallback recovery',
          status_label: 'CI failing',
          checks: {state: 'failure'},
        },
        linear: [{identifier: 'GH-2132', title: 'DeepSeek V4 validation'}],
      },
    };
    api.setTranscriptInfoForTest('4', info);

    const detail = api.tabMenuDetailText('4', info);
    const searchFields = api.tabSearchFields('4');
    assert.ok(searchFields.includes('PR'), 'tab search fields include the literal PR token');
    assert.ok(searchFields.includes('PR#9981'), 'tab search fields include PR#number');
    assert.ok(searchFields.includes('#9981'), 'tab search fields include #number');
    assert.ok(searchFields.includes('9981'), 'tab search fields include bare PR number');
    assert.ok(searchFields.includes('Parser PR description mentions fallback recovery'), 'tab search fields include the PR description');
    assert.ok(searchFields.includes('GH-2132'), 'tab search fields include Linear identifiers from issue objects');
    assert.ok(searchFields.includes('DeepSeek V4 validation'), 'tab search fields include Linear titles from issue objects');
    assert.ok(detail.includes('GH-2132__reasoning-dangling-end-marker'), 'tab menu detail includes fuller branch name');
    assert.ok(detail.includes('~/project/project3'), 'tab menu detail includes compact path');
    const prFailingLabel = api.t('pr.status.failing');
    assert.ok(detail.includes(`#9981 ${prFailingLabel}`), 'tab menu detail includes localized PR and status');
    assert.ok(detail.includes('GH-2132'), 'tab menu detail includes Linear identifier');
    const linearIndex = detail.indexOf('GH-2132', detail.indexOf('~/project/project3'));
    assert.ok(linearIndex < detail.indexOf(`#9981 ${prFailingLabel}`), 'tab menu detail lists Linear before PR');

    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.leafNode('left');
    slots.left = api.paneStateWithTabs(['4'], '4');
    api.setLayoutSlotsForTest(slots);
    const command = api.menuTabCommand('4');
    assert.ok(command.ariaLabel.includes('GH-2132__reasoning-dangling-end-marker'), 'tab menu row aria label carries detail');
    assert.ok(command.html.includes('fix(parser): parse dangling reasoning end markers'), 'tab menu row includes long PR title');
    assert.ok(command.html.includes('pane-tab-core'), 'tab menu row uses pane tab markup');

    const popover = api.sessionPopoverHtml('4', info, 'codex', true);
    assert.ok(popover.indexOf('popover-label">Linear') < popover.indexOf('popover-label">PR'), 'tab popover lists Linear before PR');
  });

  // a session is findable by an OTHER-branch PR / branch name / Linear ID — the same
  // project.git.other_branches data YO!info shows — not only its current-branch PR.
  test('t@6800', () => {
    const api = loadYolomux();
    const info = {
      selected_pane: {current_path: '/home/test/dynamo4'},
      project: {
        git: {
          branch: 'main',
          root: '/home/test/dynamo4',
          other_branches: {
            branches: [
              {
                name: 'keivenc/DIS-2193__other-work',
                current: false,
                subject: 'feat: branch subject text',
                pull_request: {number: 10289, title: 'feat: other branch work', description: 'PR body explains cut over parser wiring', linear_ids: ['DIS-2200']},
                linear_ids: ['DIS-2193'],
              },
            ],
          },
        },
      },
    };
    api.setTranscriptInfoForTest('4', info);
    const fields = api.tabSearchFields('4');
    assert.ok(fields.includes('#10289'), 'an other-branch PR is indexed as #N');
    assert.ok(fields.includes('PR#10289'), '...and as PR#N');
    assert.ok(fields.includes('10289'), '...and as a bare number');
    assert.ok(fields.includes('keivenc/DIS-2193__other-work'), 'the other branch name is indexed');
    assert.ok(fields.includes('feat: branch subject text'), 'the other branch subject is indexed');
    assert.ok(fields.includes('DIS-2193'), 'the other-branch Linear ID is indexed');
    assert.ok(fields.includes('DIS-2200'), 'the other-branch PR Linear IDs are indexed');
    assert.ok(fields.includes('feat: other branch work'), 'the other-branch PR title is indexed');
    assert.ok(fields.includes('PR body explains cut over parser wiring'), 'the other-branch PR description is indexed');
    assert.ok(Number.isFinite(api.tabSearchScore('4', 'cut over')), 'searching other-branch PR description matches the session');
    assert.ok(api.tabSearchScore('4', '#10289') >= 0, 'searching #10289 matches the session');
    assert.ok(api.tabSearchScore('4', 'DIS-2193') >= 0, 'searching the Linear ID matches the session');
    api.setCommandPaletteStateForTest('files', 'cut over');
    const visibleRows = api.commandPaletteRankItems(api.commandPaletteItems(), 'cut over').slice(0, 60);
    assert.ok(visibleRows.some(item => item.targetItem === '4'), 'Cmd-P searching cut over shows the matching pane');
  });

  await testAsync('Shift-Cmd-P opens tabs in the focused pane', async () => {
    const api = loadYolomux('', ['1', '2']);
    const slots = api.emptyLayoutSlots();
    slots.left = api.paneStateWithTabs([api.prefsItemId], api.prefsItemId);
    slots.slot1 = api.paneStateWithTabs(['2'], '2');
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
    api.setLayoutSlotsForTest(slots);
    api.setFocusedPanelItem('2');
    api.setCommandPaletteStateForTest('command', 'preferences');

    assert.equal(api.fileEditorActivationSlotForTest(), 'slot1', 'setup: focused pane is the activation target');
    const tabItem = api.commandPaletteItems().find(item => item.key === `tab:${api.prefsItemId}`);
    assert.ok(tabItem, 'Shift-Cmd-P exposes existing tabs');
    await api.invokeCommandPaletteItemForTest(tabItem);

    assert.deepStrictEqual(canonical(api.currentSlots().slot1), {tabs: ['2', api.prefsItemId], active: api.prefsItemId}, 'Shift-Cmd-P moves the tab into the focused pane instead of jumping to its old pane');
  });

  test('t@6801', () => {
    const api = loadYolomux('', ['2']);
    api.setTranscriptInfoForTest('2', {
      selected_pane: {current_path: '/home/test/dynamo/dynamo2'},
      project: {
        git: {
          branch: 'keivenchang/DIS-2223__nemotron-reasoning-end-token-stream-split',
          root: '/home/test/dynamo/dynamo2',
          other_branches: {
            branches: [
              {
                name: 'keivenchang/DIS-2223__nemotron-reasoning-end-token-stream-split',
                current: true,
                subject: 'Rework parser debug taps to always-on anomaly detection',
                pull_request: {number: 10569, title: 'Rework parser debug taps to always-on anomaly detection'},
                linear_ids: ['DIS-2223'],
              },
            ],
          },
        },
      },
    });
    api.setFileQuickOpenCandidatesForTest('/home/test/dynamo', Array.from({length: 80}, (_, index) => ({
      name: `10569-noise-${index}.txt`,
      path: `/home/test/dynamo/noise/10569-noise-${index}.txt`,
      relative_path: `noise/10569-noise-${index}.txt`,
    })));
    api.setCommandPaletteStateForTest('files', '10569');
    const fields = api.tabSearchFields('2');
    assert.ok(fields.includes('10569'), 'current branch PR from other_branches is indexed as a bare number');
    assert.ok(api.tabMenuDetailText('2').includes('#10569'), 'current branch PR from other_branches is visible in the tab detail');
    const rows = api.commandPaletteRankItems(api.commandPaletteItems(), '10569').slice(0, 8);
    const row = rows.find(item => item.targetItem === '2');
    assert.ok(row, 'Cmd-P searching the current PR number keeps the matching pane on the first screen');
    assert.ok(row.detail.startsWith('PR #10569 · '), 'Cmd-P row detail puts the matching PR number before long branch/path text');
    const popupText = api.commandPaletteResultsHtmlForTest().replace(/<[^>]+>/g, '');
    assert.ok(popupText.includes('PR #10569'), 'rendered Cmd-P popup visibly shows the matching PR number');
  });

  test('t@6802', () => {
    const api = loadYolomux('', ['wt']);
    api.setTranscriptInfoForTest('wt', {
      project: {
        git: {
          root: '/home/test/yolomux.dev3',
          cwd: '/home/test/yolomux.dev3',
          worktree: {
            path: '/home/test/yolomux.dev3',
            parent_root: '/home/test/yolomux',
            name: 'yolomux.dev3',
          },
          other_branches: {
            branches: [
              {name: 'yolomux.dev3', current: true, updated: 'today', updated_ts: 1, subject: 'worktree path row'},
            ],
          },
        },
      },
    });
    const [row] = api.infoBranchRows();
    assert.equal(row.pathLabel, '~/yolomux.dev3 (worktree of ~/yolomux)', 'YO!info path shows the compact full path and its worktree parent');
    assert.equal(row.pathTitle, '/home/test/yolomux.dev3 (worktree of /home/test/yolomux)', 'YO!info path tooltip keeps the absolute path and parent');
  });

  test('t@info-branch-worktree-path-identity', () => {
    const api = loadYolomux('', ['parent', 'linked']);
    api.setTranscriptInfoForTest('parent', {
      project: {
        git: {
          root: '/repo/main',
          branch: 'bug-10719',
          other_branches: {
            branches: [
              {name: 'bug-10719', current: true, updated: 'today', updated_ts: 300, subject: 'parent checkout branch'},
            ],
          },
        },
        repos: [],
        pull_request: null,
        linear: [],
      },
    });
    const linkedGit = {
      root: '/repo/main',
      branch: 'bug-10719',
      worktree: {
        path: '/repo/wt-bug-10719',
        parent_root: '/repo/main',
        name: 'wt-bug-10719',
      },
      other_branches: {
        branches: [
          {name: 'bug-10719', current: true, updated: 'now', updated_ts: 500, subject: 'linked checkout branch'},
        ],
      },
    };
    api.setTranscriptInfoForTest('linked', {
      agent_windows: [
        {kind: 'codex', state: 'idle', window_index: 0, git: linkedGit, path_entries: [{path: '/repo/wt-bug-10719', git: linkedGit}]},
      ],
      project: {
        git: linkedGit,
        repos: [],
        pull_request: null,
        linear: [],
      },
    });

    const rowKey = row => `${row.path}\n${row.branch}`;
    const rows = new Map(api.infoBranchRows().map(row => [rowKey(row), row]));
    assert.equal(rows.size, 2, 'YO!info keeps parent checkout and linked worktree rows separate even when branch names match');
    assert.deepStrictEqual([...rows.keys()].sort(), ['/repo/main\nbug-10719', '/repo/wt-bug-10719\nbug-10719']);
    assert.deepStrictEqual(canonical(rows.get('/repo/main\nbug-10719').tabAgents.map(item => item.label)), ['parent / no AI'], 'parent checkout keeps its own Tab/AI owner');
    assert.deepStrictEqual(canonical(rows.get('/repo/wt-bug-10719\nbug-10719').tabAgents.map(item => item.label)), ['linked / 0:codex'], 'linked worktree keeps its own Tab/AI owner');
    assert.equal(rows.get('/repo/wt-bug-10719\nbug-10719').pathLabel, '/repo/wt-bug-10719 (worktree of /repo/main)', 'linked worktree displays the checkout path and parent context');

    const relationshipKeys = api.infoRelationshipRecords()
      .filter(record => record.branchKey === 'bug-10719')
      .map(record => `${record.pathKey}|${record.tabLabel}|${record.aiLabel}`)
      .sort();
    assert.deepStrictEqual(canonical(relationshipKeys), [
      '/repo/main|parent|no AI',
      '/repo/wt-bug-10719|linked|0:codex',
    ], 'YO!info relationship records use the checkout path, not the shared parent root, as Path identity');
  });

  test('t@info-branch-repo-inventory', () => {
    const api = loadYolomux('', ['s1']);
    api.setTranscriptInfoForTest('s1', {
      project: {
        git: {
          root: '/repo/app',
          branch: 'main',
          other_branches: {
            branches: [
              {name: 'main', current: true, updated: '1 minute ago', updated_ts: 300, subject: 'app current'},
              {name: 'feature/app', current: false, updated: '2 minutes ago', updated_ts: 200, subject: 'app feature'},
            ],
          },
        },
        repos: [
          {
            root: '/repo/app',
            branch: 'main',
            other_branches: {
              branches: [
                {name: 'main', current: true, updated: '1 minute ago', updated_ts: 300, subject: 'duplicate app current'},
              ],
            },
          },
          {
            root: '/repo/lib',
            branch: 'lib-main',
            other_branches: {
              branches: [
                {name: 'lib-main', current: true, updated: 'today', updated_ts: 400, subject: 'lib current'},
                {name: 'feature/lib', current: false, updated: 'yesterday', updated_ts: 100, subject: 'lib feature'},
              ],
            },
          },
        ],
        pull_request: null,
        linear: [],
      },
    });
    const rowKey = row => `${row.path}\n${row.branch}`;
    const rows = new Map(api.infoBranchRows().map(row => [rowKey(row), row]));
    assert.deepStrictEqual(canonical(rows.get('/repo/app\nmain').tabAgents.map(item => item.label)), ['s1 / no AI'], 'YO!info lists the Tab/AI entry for the primary checked-out branch');
    assert.equal(rows.get('/repo/app\nfeature/app').session, '', 'YO!info leaves non-current primary repo branches unassigned');
    assert.deepStrictEqual(canonical(rows.get('/repo/lib\nlib-main').tabAgents.map(item => item.label)), ['s1 / no AI'], 'YO!info assigns the tab to a checked-out branch in a secondary touched repo');
    assert.equal(rows.get('/repo/lib\nfeature/lib').session, '', 'YO!info shows secondary repo branches without pretending the session owns them');
    assert.equal(rows.get('/repo/lib\nfeature/lib').updatedTs, 100, 'YO!info keeps the branch last-modified timestamp from the touched repo inventory');
    assert.equal(rows.get('/repo/lib\nfeature/lib').pathLabel, '/repo/lib', 'YO!info shows the secondary touched repo path');
  });

  test('t@info-branch-explicit-window-branch-owner', () => {
    const api = loadYolomux('', ['s1', 'shell', 'activity']);
    api.setTranscriptInfoForTest('s1', {
      agent_windows: [{kind: 'codex', state: 'idle', window_index: 1, git: {root: '/repo/app', branch: 'feature/app'}}],
      project: {
        git: {
          root: '/repo/app',
          branch: 'main',
          other_branches: {
            branches: [
              {name: 'main', current: true, updated: 'today', updated_ts: 400, subject: 'app main'},
              {name: 'feature/app', current: false, updated: 'yesterday', updated_ts: 300, subject: 'agent branch'},
              {name: 'feature/other', current: false, updated: 'last week', updated_ts: 100, subject: 'unowned branch'},
            ],
          },
        },
        pull_request: null,
        linear: [],
      },
    });
    api.setTranscriptInfoForTest('shell', {
      window_metadata: [{
        window_index: '2',
        git: {
          root: '/repo/app',
          branch: 'feature/shell',
          other_branches: {
            branches: [
              {name: 'feature/shell', current: true, updated: 'now', updated_ts: 500, subject: 'shell window branch'},
            ],
          },
        },
      }],
      project: {
        git: null,
        repos: [],
        pull_request: null,
        linear: [],
      },
    });
    api.setTranscriptInfoForTest('activity', {
      project: {
        git: null,
        repos: [],
        pull_request: null,
        linear: [],
      },
    });
    api.setTabberActivityForTest({
      activity: {},
      agents: [],
      agent_windows: {
        activity: [{
          kind: 'claude',
          state: 'working',
          window_index: 0,
          git: {
            root: '/repo/activity',
            branch: 'feature/activity',
            other_branches: {
              branches: [
                {name: 'feature/activity', current: true, updated: 'now', updated_ts: 600, subject: 'activity window branch'},
              ],
            },
          },
        }],
      },
    });

    const rowKey = row => `${row.path}\n${row.branch}`;
    const rows = new Map(api.infoBranchRows().map(row => [rowKey(row), row]));
    assert.deepStrictEqual(canonical(rows.get('/repo/app\nfeature/app').tabAgents.map(item => item.label)), ['s1 / 1:codex'], 'YO!info assigns a non-current branch to the AI window whose git metadata names that branch');
    assert.equal(rows.get('/repo/app\nfeature/other').session, '', 'YO!info keeps unrelated non-current branches unassigned');
    assert.deepStrictEqual(canonical(rows.get('/repo/app\nfeature/shell').tabAgents.map(item => item.label)), ['shell / no AI'], 'YO!info uses tmux sub-window git metadata as a branch source even without an AI window');
    assert.deepStrictEqual(canonical(rows.get('/repo/activity\nfeature/activity').tabAgents.map(item => item.label)), ['activity / 0:claude'], 'YO!info uses activity agent-window git metadata as a branch source when transcript project metadata has no repo inventory');

    const records = api.infoRelationshipRecords();
    const featureRecord = records.find(record => record.pathKey === '/repo/app' && record.branchKey === 'feature/app');
    assert.equal(`${featureRecord?.tabLabel}|${featureRecord?.aiLabel}`, 's1|1:codex', 'YO!info relationship records carry explicit non-current branch ownership instead of No tab / No AI');
    assert.equal(records.some(record => record.pathKey === '/repo/app' && record.branchKey === 'feature/app' && record.tabLabel === 'No tab'), false, 'YO!info does not emit a No tab fallback for an explicitly owned branch');
  });

  test('t@info-branch-tab-ai-aggregation', () => {
    const api = loadYolomux('', ['s1', 's2']);
    const project = {
      git: {
        root: '/repo/app',
        branch: 'main',
        other_branches: {
          branches: [
            {name: 'main', current: true, updated: '1 minute ago', updated_ts: 300, subject: 'app current'},
          ],
        },
      },
      pull_request: null,
      linear: [],
    };
    api.setTranscriptInfoForTest('s1', {
      agent_windows: [{kind: 'claude', state: 'working', window_index: 0, git: {root: '/repo/app', branch: 'main'}}],
      project,
    });
    api.setTranscriptInfoForTest('s2', {
      agent_windows: [{kind: 'codex', state: 'idle', window_index: 1, git: {root: '/repo/app', branch: 'main'}}],
      project,
    });

    const [row] = api.infoBranchRows();

    assert.equal(row.path, '/repo/app');
    assert.equal(row.branch, 'main');
    assert.deepStrictEqual(canonical(row.tabAgents.map(item => item.label)), ['s1 / 0:claude', 's2 / 1:codex'], 'YO!info aggregates every Tab/AI pair for a path+branch row');
    assert.equal(row.session, 's1 / 0:claude, s2 / 1:codex', 'the legacy sort/share text follows the aggregated Tab/AI labels');
  });

  test('t@info-tree-active-sub-window-follows-tmux-signals', () => {
    const api = loadYolomux('', ['meta-preview']);
    const project = {
      git: {
        root: '/repo/app',
        branch: 'main',
        other_branches: {
          branches: [
            {name: 'main', current: true, updated: 'now', updated_ts: 10, subject: 'main'},
          ],
        },
      },
      pull_request: null,
      linear: [],
    };
    api.setTranscriptInfoForTest('meta-preview', {
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/app'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/app'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'claude', command: 'claude', current_path: '/repo/app'},
      ],
      agent_windows: [
        {kind: 'codex', state: 'idle', current: true, window_active: true, window_index: 0, git: {root: '/repo/app', branch: 'main'}},
        {kind: 'claude', state: 'idle', current: false, window_active: false, window_index: 1, git: {root: '/repo/app', branch: 'main'}},
      ],
      project,
    });

    api.applyTmuxSignalsPayloadForTest({windows: [{
      session: 'meta-preview',
      window_index: '0',
      active: false,
      panes: [{target: 'meta-preview:0.0', pane_id: 'meta-preview:0.0', pane_index: '0', window_index: '0', active: true, current_path: '/repo/app', current_command: 'codex'}],
    }, {
      session: 'meta-preview',
      window_index: '1',
      active: true,
      panes: [{target: 'meta-preview:1.0', pane_id: 'meta-preview:1.0', pane_index: '0', window_index: '1', active: true, current_path: '/repo/app', current_command: 'claude'}],
    }]});

    const rows = api.sessionAgentWindowStatusPayloadsForTest('meta-preview', api.transcriptInfoForTest('meta-preview'));
    assert.equal(rows.find(row => row.window_index === 0)?.window_active, false, 'stale agent_windows current flags are cleared when tmux says window 0 is inactive');
    assert.equal(rows.find(row => row.window_index === 1)?.window_active, true, 'stale agent_windows current flags follow the active tmux sub-window');
    const records = api.infoRelationshipRecords();
    assert.equal(records.find(record => record.aiWindow === '0')?.aiWindowActive, false, 'YO!info record state clears the old active sub-window');
    assert.equal(records.find(record => record.aiWindow === '1')?.aiWindowActive, true, 'YO!info record state follows the switched tmux sub-window');
    const html = api.infoTreeHtmlForTest(records, ['path']);
    assert.ok(/class="tab tmux-window-button info-tree-ai-window-button active"[\s\S]*data-info-open-ai-window="1"/.test(html), 'YO!info renders the switched tmux sub-window as the active Info Bar-style button');
    assert.equal(/class="tab tmux-window-button info-tree-ai-window-button active"[\s\S]*data-info-open-ai-window="0"/.test(html), false, 'YO!info does not keep the previous sub-window button active after a tmux switch');
    const activitySource = fs.readFileSync('static_src/js/yolomux/45_agent_window_activity.js', 'utf8');
    assert.ok(/function sessionAgentWindowStatusModel[\s\S]*activeTmuxWindowIndexFromInfo\(info\)[\s\S]*agentWindowWithInfoActiveWindow\(agent, activeIndex\)[\s\S]*function sessionAgentWindowStatusPayloads[\s\S]*sessionAgentWindowStatusModel\(session, info, autoPayload\)\.agents/.test(activitySource), 'the shared status model normalizes current/window_active from live tmux pane state before every consumer, including YO!info, reads it');
  });

  test('t@info-tree-relationship-grouping', () => {
    const api = loadYolomux('', ['tab-a', 'tab-b', '1']);
    const appProject = {
      git: {
        root: '/repo/app',
        branch: 'main',
        other_branches: {
          branches: [
            {name: 'main', current: true, updated: 'today', updated_ts: 500, subject: 'app main', pull_request: {number: 10, url: 'https://example.test/pull/10', title: 'App main PR full description', state: 'open', checks: {state: 'failure', summary: 'CI error'}}, linear_ids: ['DYN-10'], linear: [{identifier: 'DYN-10', title: 'Main Linear description', url: 'https://linear.test/DYN-10'}]},
            {name: 'feature/app', current: false, updated: 'yesterday', updated_ts: 200, subject: 'app feature', pull_request: {number: 11, url: 'https://example.test/pull/11', title: 'App feature PR linked through path', merged: true}},
          ],
        },
      },
      repos: [
        {
          root: '/repo/lib',
          branch: 'lib-main',
          other_branches: {
            branches: [
              {name: 'lib-main', current: true, updated: 'today', updated_ts: 450, subject: 'lib main'},
            ],
          },
        },
      ],
      pull_request: null,
      linear: [],
    };
    api.setTranscriptInfoForTest('tab-a', {
      agent_windows: [
        {kind: 'claude', state: 'working', window_index: 0, git: {root: '/repo/app', branch: 'main'}},
        {kind: 'codex', state: 'idle', window_index: 0, git: {root: '/repo/lib', branch: 'lib-main'}},
      ],
      project: appProject,
    });
    api.setTranscriptInfoForTest('tab-b', {
      agent_windows: [
        {kind: 'codex', state: 'needs-input', window_index: 0, git: {root: '/repo/app', branch: 'main'}},
      ],
      project: appProject,
    });

    const records = api.infoRelationshipRecords();
    assert.deepStrictEqual(canonical(records.map(record => `${record.tabLabel}|${record.aiLabel}|${record.pathKey}|${record.branchKey}`)), [
      'tab-a|0:claude|/repo/app|main',
      'tab-b|0:codex|/repo/app|main',
      'tab-a|0:codex|/repo/lib|lib-main',
      'tab-a|0:claude|/repo/app|feature/app',
      'tab-b|0:codex|/repo/app|feature/app',
    ], 'YO!info emits direct branch owners first, then path-level Tab/AI relationships for branches without direct owners');
    assert.equal(records.some(record => record.prLabel === '#11 App feature PR linked through path' && record.tabLabel === 'No tab'), false, 'YO!info does not list a PR as No tab when its branch path is tied to a Tab');
    const appFeatureRecord = records.find(record => record.pathKey === '/repo/app' && record.branchKey === 'feature/app' && record.tabLabel === 'tab-a');
    assert.equal(appFeatureRecord?.prLabel, '#11', 'YO!info relationship records keep the PR number as the compact clickable label');
    assert.equal(appFeatureRecord?.prTitle, '#11 App feature PR linked through path', 'YO!info relationship records keep the full PR title beside the linked number');
    assert.equal(appFeatureRecord?.prUrl, 'https://example.test/pull/11', 'YO!info relationship records carry the PR URL so the PR number is clickable');
    assert.equal(appFeatureRecord?.prLifecycleText, 'MERGED', 'YO!info relationship records carry PR lifecycle status for merged badges');
    const appMainRecord = records.find(record => record.pathKey === '/repo/app' && record.branchKey === 'main' && record.tabLabel === 'tab-a');
    assert.equal(appMainRecord?.tabLabel, 'tab-a', 'YO!info keeps the bare session identity separate from the displayed work description');
    assert.equal(Object.hasOwn(appMainRecord, 'tabWorkDescription'), false, 'YO!info relationships do not carry a parallel Tab detail that can drift from the shared tab renderer');
    assert.equal(appMainRecord?.prLifecycleText, 'OPEN', 'YO!info relationship records carry explicit Open lifecycle status');
    assert.equal(appMainRecord?.prCiText, 'CI error', 'YO!info relationship records carry CI status separately from PR description');
    assert.equal(appMainRecord?.aiState, 'working', 'YO!info relationship records carry agent-window state for activity dots');
    assert.deepStrictEqual(canonical(api.infoFilteredRecordsForTest(records, 'feature linked').map(record => `${record.tabLabel}|${record.branchLabel}|${record.prTitle}`)), [
      'tab-a|feature/app|#11 App feature PR linked through path',
      'tab-b|feature/app|#11 App feature PR linked through path',
    ], 'YO!info search filters relationships by fuzzy PR description matches');
    assert.deepStrictEqual(canonical(api.infoFilteredRecordsForTest(records, 'DYN10').map(record => record.branchLabel)), ['main', 'main'], 'YO!info search matches Linear identifiers without requiring exact punctuation');
    assert.deepStrictEqual(canonical(api.infoFilteredRecordsForTest(records, 'codx').map(record => `${record.tabLabel}|${record.aiLabel}|${record.pathLabel}`)), [
      'tab-b|0:codex|/repo/app',
      'tab-a|0:codex|/repo/lib',
      'tab-b|0:codex|/repo/app',
    ], 'YO!info search can match a fuzzy tmux sub-window label inside the AI field');
    assert.deepStrictEqual(canonical(api.infoFilteredRecordsForTest(records, 'lib-main').map(record => `${record.tabLabel}|${record.aiLabel}|${record.pathLabel}`)), [
      'tab-a|0:codex|/repo/lib',
    ], 'YO!info search can match branch text inside the Branch field');
    assert.deepStrictEqual(canonical(api.infoFilteredRecordsForTest(records, 'codx lib-main').map(record => `${record.tabLabel}|${record.aiLabel}|${record.pathLabel}`)), [], 'YO!info search does not combine one query across different field types');
    const visibleSearchRecords = [
      {id: 'tab-7777', tabKey: '7777', tabLabel: '7777', tabTitle: '7777', tabSession: '7777', aiKey: 'ai-0', aiKind: 'codex', aiWindow: '0', aiWindowIndex: 0, aiLabel: '0:codex', pathKey: '/repo/no-match', pathLabel: '/repo/no-match', pathTitle: '/repo/no-match', branchKey: 'main', branchLabel: 'main', branchTitle: 'main'},
      {id: 'split-seven', tabKey: '7', tabLabel: '7', tabTitle: '7', tabSession: '7', aiKey: 'ai-7', aiKind: 'codex', aiWindow: '7', aiWindowIndex: 7, aiLabel: '7:codex', pathKey: '/repo/7-path', pathLabel: '/repo/7-path', pathTitle: '/repo/7-path', branchKey: 'branch-7', branchLabel: 'branch-7', branchTitle: 'branch-7', prKey: '#70', prLabel: '#70', prTitle: '#70 one seven only', prNumber: 70, linearKey: 'DIS-7', linearLabel: 'DIS-7', linearTitle: 'DIS-7 one seven only'},
    ];
    assert.deepStrictEqual(canonical(api.infoFilteredRecordsForTest(visibleSearchRecords, '777').map(record => record.id)), ['tab-7777'], 'YO!info search does not assemble one fuzzy token from separate Tab/AI/Path/Branch/PR/Linear fields');
    api.setInfoSearchForTest('777', {publish: false});
    const searchHighlightHtml = api.infoTreeHtmlForTest(api.infoFilteredRecordsForTest(visibleSearchRecords, '777'), ['tab', 'path']);
    assert.ok(/class="info-tree-search-match">777<\/mark>7/.test(searchHighlightHtml), 'YO!info highlights the visible matched Tab session text');
    assert.equal(searchHighlightHtml.includes('split-seven'), false, 'YO!info search hides records that only contain split non-matching 7 values');
    api.setInfoSearchForTest('', {publish: false});
    api.setInfoSearchForTest('feature linked', {publish: false});
    assert.equal(api.currentInfoSearchForTest(), 'feature linked', 'YO!info stores the current search text');
    assert.ok(api.infoTreeHtmlForTest(api.infoFilteredRecordsForTest(records, api.currentInfoSearchForTest()), ['path', 'branch']).includes('data-info-search="feature linked"'), 'YO!info tree html records the active search query for DOM/share diagnostics');
    api.setInfoSearchForTest('', {publish: false});

    const tabTree = api.infoGroupTree(records, ['tab', 'ai', 'path', 'branch']);
    assert.deepStrictEqual(canonical(tabTree.children.map(group => `${group.dimension}:${group.label}:${group.count}`)), [
      'tab:tab-a:3',
      'tab:tab-b:2',
    ], 'grouping by Tab starts with Tab buckets and preserves per-tab relationship counts');

    const pathTree = api.infoGroupTree(records, ['path', 'branch', 'tab', 'ai'], {key: 'branch', dir: 'asc'});
    assert.deepStrictEqual(canonical(pathTree.children.map(group => `${group.dimension}:${group.label}:${group.count}`)), [
      'path:/repo/app:4',
      'path:/repo/lib:1',
    ], 'grouping by Path starts with Path buckets and shows one path can own multiple Tab/AI records');
    assert.deepStrictEqual(canonical(pathTree.children.find(group => group.label === '/repo/app').children.map(group => `${group.dimension}:${group.label}:${group.count}`)), [
      'branch:feature/app:2',
      'branch:main:2',
    ], 'path grouping nests branch buckets below each path');

    const prTree = api.infoGroupTree(records, ['pr', 'path', 'branch', 'tab'], {key: 'pr', dir: 'asc'});
    assert.deepStrictEqual(canonical(prTree.children.map(group => `${group.dimension}:${group.key}:${group.label}:${group.count}`)), [
      'pr:#10:#10 App main PR full description:2',
      'pr:#11:#11 App feature PR linked through path:2',
      'pr:__no_pr__:No PR:1',
    ], 'grouping by PR keeps the stable compact PR key but shows path-linked PRs under related Tab/AI records');

    const prDescTree = api.infoGroupTree(records, ['pr', 'path', 'branch', 'tab'], {key: 'pr', dir: 'desc'});
    assert.deepStrictEqual(canonical(prDescTree.children.map(group => group.label)), [
      '#11 App feature PR linked through path',
      '#10 App main PR full description',
      'No PR',
    ], 'YO!info PR sorting can be reversed while keeping rows without PRs last');

    const dateAscTree = api.infoGroupTree(records, ['pr', 'path', 'branch', 'tab'], {key: 'date', dir: 'asc'});
    assert.deepStrictEqual(canonical(dateAscTree.children.map(group => group.label)), [
      '#11 App feature PR linked through path',
      'No PR',
      '#10 App main PR full description',
    ], 'YO!info can sort grouped PR records by branch date oldest first');

    const tabDescTree = api.infoGroupTree(records, ['tab', 'ai', 'path', 'branch'], {key: 'tab', dir: 'desc'});
    assert.deepStrictEqual(canonical(tabDescTree.children.map(group => group.label)), [
      'tab-b',
      'tab-a',
    ], 'YO!info can sort group headers by Tab name in reverse lexical order');

    const numericPrTree = api.infoGroupTree([
      {prKey: '#1111', prLabel: '#1111 large PR', prTitle: '#1111 large PR', prNumber: 1111},
      {prKey: '#9', prLabel: '#9 something', prTitle: '#9 something', prNumber: 9},
      {prKey: '__no_pr__', prLabel: 'No PR', prTitle: 'No PR'},
    ], ['pr'], {key: 'pr', dir: 'asc'});
    assert.deepStrictEqual(canonical(numericPrTree.children.map(group => group.label)), [
      '#9 something',
      '#1111 large PR',
      'No PR',
    ], 'YO!info PR grouping sorts by PR number instead of lexical label order');
    const numericPrDescTree = api.infoGroupTree([
      {prKey: '#1111', prLabel: '#1111 large PR', prTitle: '#1111 large PR', prNumber: 1111},
      {prKey: '#9', prLabel: '#9 something', prTitle: '#9 something', prNumber: 9},
      {prKey: '__no_pr__', prLabel: 'No PR', prTitle: 'No PR'},
    ], ['pr'], {key: 'name', dir: 'desc'});
    assert.deepStrictEqual(canonical(numericPrDescTree.children.map(group => group.label)), [
      '#1111 large PR',
      '#9 something',
      'No PR',
    ], 'YO!info PR grouping reverses by extracted PR number while keeping missing PRs last');
    const numericLinearTree = api.infoGroupTree([
      {linearKey: 'DIS-1111', linearLabel: 'DIS-1111', linearTitle: 'DIS-1111 large issue'},
      {linearKey: 'DIS-9', linearLabel: 'DIS-9', linearTitle: 'DIS-9 small issue'},
      {linearKey: '__no_linear__', linearLabel: 'No Linear', linearTitle: 'No Linear'},
    ], ['linear'], {key: 'name', dir: 'asc'});
    assert.deepStrictEqual(canonical(numericLinearTree.children.map(group => group.label)), [
      'DIS-9 small issue',
      'DIS-1111 large issue',
      'No Linear',
    ], 'YO!info Linear grouping sorts by extracted issue number instead of lexical label order');
    const numericTabTree = api.infoGroupTree([
      {tabKey: 'tab-1111', tabLabel: 'tab-1111'},
      {tabKey: 'tab-9', tabLabel: 'tab-9'},
      {tabKey: '__no_tab__', tabLabel: 'No tab'},
    ], ['tab'], {key: 'name', dir: 'asc'});
    assert.deepStrictEqual(canonical(numericTabTree.children.map(group => group.label)), [
      'tab-9',
      'tab-1111',
      'No tab',
    ], 'YO!info Tab grouping sorts by the first extracted number before lexical fallback');
    const lexicalBranchPathRecords = [
      {pathKey: '/repo/path-1111', pathLabel: '/repo/path-1111', branchKey: 'branch-1111', branchLabel: 'branch-1111'},
      {pathKey: '/repo/path-9', pathLabel: '/repo/path-9', branchKey: 'branch-9', branchLabel: 'branch-9'},
    ];
    assert.deepStrictEqual(canonical(api.infoGroupTree(lexicalBranchPathRecords, ['branch'], {key: 'name', dir: 'asc'}).children.map(group => group.label)), [
      'branch-1111',
      'branch-9',
    ], 'YO!info Branch grouping stays lexical instead of extracting numbers');
    assert.deepStrictEqual(canonical(api.infoGroupTree(lexicalBranchPathRecords, ['path'], {key: 'name', dir: 'asc'}).children.map(group => group.label)), [
      '/repo/path-1111',
      '/repo/path-9',
    ], 'YO!info Path grouping stays lexical instead of extracting numbers');
	    const missingSortRecords = [
	      {tabKey: '__no_tab__', tabLabel: 'No tab', aiKey: 'no-ai::No AI', aiLabel: 'No AI', pathKey: '__no_path__', pathLabel: 'No path', branchKey: '__no_branch__', branchLabel: 'No branch', prKey: '__no_pr__', prLabel: 'No PR', prTitle: 'No PR', linearKey: '__no_linear__', linearLabel: 'No Linear', linearTitle: 'No Linear'},
	      {tabKey: 'zeta-tab', tabSession: 'zeta-tab', tabLabel: 'zeta-tab', aiKey: 'z:codex', aiKind: 'codex', aiWindow: 'z', aiWindowIndex: 'z', aiLabel: 'z:codex', pathKey: '/zeta', pathLabel: '/zeta', branchKey: 'zeta', branchLabel: 'zeta', prKey: '#200', prLabel: '#200 zeta PR', prTitle: '#200 zeta PR', prNumber: 200, linearKey: 'ZZZ-200', linearLabel: 'ZZZ-200', linearTitle: 'ZZZ-200 zeta issue'},
	      {tabKey: 'alpha-tab', tabSession: 'alpha-tab', tabLabel: 'alpha-tab', aiKey: '0:claude', aiKind: 'claude', aiWindow: '0', aiWindowIndex: '0', aiLabel: '0:claude', pathKey: '/alpha', pathLabel: '/alpha', branchKey: 'alpha', branchLabel: 'alpha', prKey: '#100', prLabel: '#100 alpha PR', prTitle: '#100 alpha PR', prNumber: 100, linearKey: 'AAA-100', linearLabel: 'AAA-100', linearTitle: 'AAA-100 alpha issue'},
	    ];
	    assert.deepStrictEqual(canonical(api.infoGroupTree(missingSortRecords, ['tab'], {key: 'tab', dir: 'asc'}).children.map(group => group.label)), ['alpha-tab', 'zeta-tab', 'No tab'], 'YO!info A-Z Tab grouping treats No tab as after z');
	    assert.deepStrictEqual(canonical(api.infoGroupTree(missingSortRecords, ['tab', 'tmux-window'], {key: 'name', dir: 'asc'}).children[0].children.map(group => group.label)), ['0:claude'], 'YO!info tmux sub-window grouping sorts by window index under the Tab parent');
	    assert.deepStrictEqual(canonical(api.infoGroupTree(missingSortRecords, ['tab', 'ai'], {key: 'ai', dir: 'asc'}).children[0].children.map(group => group.label)), ['claude'], 'YO!info AI grouping sorts by agent identity under the Tab parent');
	    assert.deepStrictEqual(canonical(api.infoGroupTree(missingSortRecords, ['linear'], {key: 'linear', dir: 'asc'}).children.map(group => group.label)), ['AAA-100 alpha issue', 'ZZZ-200 zeta issue', 'No Linear'], 'YO!info A-Z Linear grouping treats No Linear as after z');
	    assert.deepStrictEqual(canonical(api.infoGroupTree(missingSortRecords, ['pr'], {key: 'pr', dir: 'asc'}).children.map(group => group.label)), ['#100 alpha PR', '#200 zeta PR', 'No PR'], 'YO!info A-Z PR grouping treats No PR as after numbered PRs');
    assert.deepStrictEqual(canonical(api.infoGroupTree(missingSortRecords, ['linear'], {key: 'linear', dir: 'desc'}).children.map(group => group.label)), ['ZZZ-200 zeta issue', 'AAA-100 alpha issue', 'No Linear'], 'YO!info reverse Linear grouping keeps No Linear after real Linear issues');
    assert.deepStrictEqual(canonical(api.infoGroupTree(missingSortRecords, ['pr'], {key: 'pr', dir: 'desc'}).children.map(group => group.label)), ['#200 zeta PR', '#100 alpha PR', 'No PR'], 'YO!info reverse PR grouping keeps No PR after numbered PRs');
	    assert.deepStrictEqual(canonical(api.infoGroupingPresetsForTest().map(preset => `${preset.key}:${preset.label}:${preset.grouping.join('>')}`)), [
	      'tab-tmux-window:Tab > tmux-window:tab>tmux-window',
	      'tab-path:Tab > Path > tmux-window:tab>path>tmux-window',
	      'path-branch:Path > Branch:path>branch',
	      'linear-pr:Linear > PR:linear>pr',
	      'pr-branch:PR > Branch:pr>branch',
	    ], 'YO!info quick preset buttons abbreviate tmux sub-window as tmux-window');
	    assert.deepStrictEqual(canonical(api.currentInfoGroupingForTest()), ['tab', 'path', 'tmux-window'], 'YO!info defaults to Tab > Path > tmux sub-window');
	    api.setInfoGroupingPresetForTest('tab-tmux-window');
	    assert.deepStrictEqual(canonical(api.currentInfoGroupingForTest()), ['tab', 'tmux-window'], 'YO!info first quick preset selects Tab then tmux sub-window');
	    api.setInfoGroupingPresetForTest('tab-path');
	    assert.deepStrictEqual(canonical(api.currentInfoGroupingForTest()), ['tab', 'path', 'tmux-window'], 'YO!info Tab > Path preset adds tmux sub-window as its third level');
	    api.setInfoGroupingPresetForTest('linear-pr');
    assert.deepStrictEqual(canonical(api.currentInfoGroupingForTest()), ['linear', 'pr'], 'YO!info Linear > PR quick preset selects Linear then PR');
    api.setInfoGroupingPresetForTest('pr-branch');
    assert.deepStrictEqual(canonical(api.currentInfoGroupingForTest()), ['pr', 'branch'], 'YO!info PR > Branch quick preset selects PR then Branch');
    const storedGroupingFor = grouping => loadYolomux('', ['stored'], 'http:', 'Linux x86_64', 'admin', {
      localStorage: {'yolomux.info2.grouping.v1': JSON.stringify(grouping)},
    }).currentInfoGroupingForTest();
	    assert.deepStrictEqual(canonical(storedGroupingFor(['tab', 'ai', 'path', 'branch'])), ['tab', 'path', 'tmux-window'], 'YO!info migrates the old stored Tab-first quick grouping to Tab > Path > tmux sub-window');
	    assert.deepStrictEqual(canonical(storedGroupingFor(['path', 'branch', 'tab', 'ai'])), ['path', 'branch'], 'YO!info migrates the old stored Path-first quick grouping to Path > Branch');
	    assert.deepStrictEqual(canonical(storedGroupingFor(['branch', 'path', 'tab', 'ai'])), ['path', 'branch'], 'YO!info migrates the removed stored branch-first quick grouping to Path > Branch');
	    assert.deepStrictEqual(canonical(storedGroupingFor(['ai', 'tab', 'path', 'branch'])), ['linear', 'pr'], 'YO!info migrates the removed stored AI-first quick grouping to Linear > PR');
	    api.setInfoGroupingForTest(['ai', 'tab', 'path', 'branch']);
	    assert.deepStrictEqual(canonical(api.currentInfoGroupingForTest()), ['tab', 'path', 'branch'], 'YO!info manual grouping selectors reject AI in the first Order by dropdown');
	    api.setInfoGroupingForTest(['tab', 'path', 'ai']);
	    assert.deepStrictEqual(canonical(api.currentInfoGroupingForTest()), ['tab', 'path', 'ai'], 'YO!info manual grouping selectors still allow AI after the first Order by dropdown');
	    api.setInfoGroupingForTest(['path', 'tmux-window', 'branch']);
	    assert.deepStrictEqual(canonical(api.currentInfoGroupingForTest()), ['path', 'branch'], 'YO!info rejects tmux sub-window unless the first Order by dropdown is Tab');
	    api.setInfoGroupingForTest(['tab', 'tmux-window', 'ai', 'path']);
	    assert.deepStrictEqual(canonical(api.currentInfoGroupingForTest()), ['tab', 'tmux-window', 'ai', 'path'], 'YO!info allows tmux sub-window as the second Order by dropdown after Tab');
	    api.setInfoGroupingForTest(['tab', 'path', 'tmux-window']);
	    assert.deepStrictEqual(canonical(api.currentInfoGroupingForTest()), ['tab', 'path', 'tmux-window'], 'YO!info allows tmux sub-window as the third Order by dropdown after Tab');
	    api.setInfoGroupingForTest(['tab', 'path', 'branch', 'tmux-window']);
	    assert.deepStrictEqual(canonical(api.currentInfoGroupingForTest()), ['tab', 'path', 'branch', 'tmux-window'], 'YO!info allows tmux sub-window as the fourth Order by dropdown after Tab');
	    const optionValuesForLevel = (html, level) => {
	      const match = html.match(new RegExp(`<select data-info-group-level="${level}"[^>]*>([\\s\\S]*?)<\\/select>`));
	      assert.ok(match, `YO!info renders Order by select ${level + 1}`);
	      return [...match[1].matchAll(/<option value="([^"]*)"/g)].map(matchItem => matchItem[1]);
	    };
	    const tabFirstControlsHtml = api.infoGroupingControlsHtmlForTest();
	    assert.ok(!optionValuesForLevel(tabFirstControlsHtml, 0).includes('ai') && !optionValuesForLevel(tabFirstControlsHtml, 0).includes('tmux-window'), 'YO!info first Order by dropdown excludes AI and tmux sub-window');
	    assert.ok(optionValuesForLevel(tabFirstControlsHtml, 1).includes('tmux-window') && optionValuesForLevel(tabFirstControlsHtml, 1).includes('ai'), 'YO!info second Order by dropdown includes tmux sub-window and AI when the first dropdown is Tab');
	    assert.equal(optionValuesForLevel(tabFirstControlsHtml, 2).includes('tmux-window'), true, 'YO!info third Order by dropdown includes tmux sub-window when the first dropdown is Tab');
	    assert.equal(optionValuesForLevel(tabFirstControlsHtml, 3).includes('tmux-window'), true, 'YO!info fourth Order by dropdown includes tmux sub-window when the first dropdown is Tab');
	    api.setInfoGroupingForTest(['path', 'branch']);
	    const pathFirstControlsHtml = api.infoGroupingControlsHtmlForTest();
	    assert.equal(optionValuesForLevel(pathFirstControlsHtml, 1).includes('tmux-window'), false, 'YO!info hides tmux sub-window when the first Order by dropdown is not Tab');

	    const tabAiTree = api.infoGroupTree(records, ['tab', 'ai', 'path', 'branch']);
	    assert.deepStrictEqual(canonical(tabAiTree.children.find(group => group.label === 'tab-a').children.map(group => `${group.dimension}:${group.label}:${group.count}`)), ['ai:claude:2', 'ai:codex:1'], 'grouping by AI under Tab groups by agent identity, not by tmux sub-window label');
	    const tmuxWindowTree = api.infoGroupTree(records, ['tab', 'tmux-window', 'path']);
	    assert.deepStrictEqual(canonical(tmuxWindowTree.children.find(group => group.label === 'tab-a').children.map(group => `${group.dimension}:${group.label}:${group.count}`)), ['tmux-window:0:claude:2', 'tmux-window:0:codex:1'], 'grouping by tmux sub-window under Tab groups by the owning sub-window label');
	    const html = api.infoTreeHtmlForTest(records, ['tab', 'tmux-window', 'path']);
	    assert.ok(html.includes('data-info-grouping="tab,tmux-window,path"') && html.includes('data-info-sort="date:desc"') && html.includes('data-info-dimension="tmux-window"') && html.includes('<span class="info-tree-group-dimension">tmux sub-window:</span>') && html.includes('0:claude') && html.includes('0:codex'), 'YO!info renders a tree for Tab > tmux sub-window and the selected sort order');
	    assert.equal((api.infoTreeHtmlForTest([appMainRecord], ['tab', 'tmux-window']).match(/info-tree-field-ai/g) || []).length, 0, 'YO!info hides tmux sub-window leaf rows when the tmux sub-window is already supplied by an ancestor group');
	    assert.ok(html.includes('info-tree-item-last'), 'YO!info marks the final child at each tree level so CSS can draw an angle connector instead of a tee');
    const appPathGroup = api.infoGroupTree(records, ['path']).children.find(group => group.dimension === 'path' && group.key === '/repo/app');
    const appPathGroupKey = api.infoTreeGroupCollapseKeyForTest(appPathGroup);
    api.setInfoTreeGroupCollapsedForTest(appPathGroupKey, true);
    const collapsedPathHtml = api.infoTreeHtmlForTest(records, ['path']);
    const collapsedPathMarker = `data-info-group-key="${appPathGroupKey}"`;
    const collapsedPathTag = collapsedPathHtml.slice(collapsedPathHtml.lastIndexOf('<details', collapsedPathHtml.indexOf(collapsedPathMarker)), collapsedPathHtml.indexOf('>', collapsedPathHtml.indexOf(collapsedPathMarker)) + 1);
    assert.ok(collapsedPathTag.includes('data-info-dimension="path"') && !collapsedPathTag.includes(' open'), 'YO!info preserves a collapsed group when the tree HTML is regenerated');
    api.setInfoTreeGroupCollapsedForTest(appPathGroupKey, false);
    const prGroupHtml = api.infoTreeHtmlForTest(records, ['pr'], {key: 'name', dir: 'asc'});
    assert.ok(/<span class="info-tree-group-dimension">GitHub PR:<\/span>[\s\S]*<span class="info-tree-group-label info-tree-group-label-pr">[\s\S]*>#10<\/a>[\s\S]*App main PR full description/.test(prGroupHtml), 'YO!info PR group headers render as PR: #number description');
    assert.ok(/<span class="info-tree-group-dimension">GitHub PR:<\/span>[\s\S]*<span class="info-tree-group-label info-tree-group-label-pr">None<\/span>/.test(prGroupHtml), 'YO!info missing PR group headers render as PR: None');
    const linearGroupHtml = api.infoTreeHtmlForTest(missingSortRecords, ['linear', 'pr'], {key: 'name', dir: 'asc'});
    assert.ok(/<span class="info-tree-group-dimension">Linear:<\/span>[\s\S]*<span class="info-tree-group-label info-tree-group-label-linear">None<\/span>/.test(linearGroupHtml), 'YO!info missing Linear group headers render as Linear: None');
    const pathOnlyHtml = api.infoTreeHtmlForTest(records, ['path']);
    assert.equal((pathOnlyHtml.match(/info-tree-field-path/g) || []).length, 0, 'YO!info hides path rows when Path is already the parent group');
    assert.ok(pathOnlyHtml.includes('<span class="info-tree-field-label">Git branch:</span>') && pathOnlyHtml.includes('feature/app') && pathOnlyHtml.includes('lib-main'), 'YO!info leaf rows show labeled Git branch identities when Branch is not already supplied by an ancestor group');
    assert.ok(pathOnlyHtml.includes('<span class="info-tree-field-label">GitHub PR:</span>') && pathOnlyHtml.includes('#11') && pathOnlyHtml.includes('App feature PR linked through path'), 'YO!info leaf rows show labeled PR descriptions when a PR exists');
    assert.ok(/<span class="info-tree-field-label">GitHub PR:<\/span>[\s\S]*<a href="https:\/\/example\.test\/pull\/11"[\s\S]*>#11<\/a>[\s\S]*App feature PR linked through path/.test(pathOnlyHtml), 'YO!info generated rows make the PR number clickable and render the PR description after it');
    assert.ok(/<span class="info-tree-field-label">GitHub PR:<\/span>[\s\S]*<a href="https:\/\/example\.test\/pull\/10"[\s\S]*>#10<\/a>[\s\S]*App main PR full description[\s\S]*ci-indicator tab-symbol pr-status-open[\s\S]*OPEN[\s\S]*ci-indicator tab-symbol pr-status-failing[\s\S]*CI error/.test(pathOnlyHtml), 'YO!info PR rows reuse the same compact status badges as tabs beside the full description');
    assert.ok(/<span class="info-tree-field-label">GitHub PR:<\/span>[\s\S]*<a href="https:\/\/example\.test\/pull\/11"[\s\S]*>#11<\/a>[\s\S]*App feature PR linked through path[\s\S]*ci-indicator tab-symbol pr-status-merged[\s\S]*MERGED/.test(pathOnlyHtml), 'YO!info PR rows reuse the shared purple merged badge');
    assert.ok(/<span class="info-tree-field-label">Linear:<\/span>[\s\S]*<a href="https:\/\/linear\.test\/DYN-10"[\s\S]*>DYN-10<\/a>[\s\S]*Main Linear description/.test(pathOnlyHtml), 'YO!info generated rows make the Linear identifier clickable and render the Linear description after it');
    assert.ok(pathOnlyHtml.includes('data-info-open-tab="tab-a"') && pathOnlyHtml.includes('data-info-open-ai-window="0"'), 'YO!info Tab and AI fields are actionable links to the owning tab/window');
    assert.ok(pathOnlyHtml.includes('agent-window-status-dot') && pathOnlyHtml.includes('status-indicator--working') && pathOnlyHtml.includes('status-indicator--attention') && !/\bA(?:S)K\b/.test(pathOnlyHtml), 'YO!info AI rows show shared working/attention activity indicators without a text attention label');
    assert.ok(/info-tree-ai-window-token[\s\S]*data-tmux-window-bar-context="info"[\s\S]*class="tab tmux-window-button info-tree-ai-window-button[^"]*"[\s\S]*data-info-open-ai-window="0"[\s\S]*0:claude/.test(pathOnlyHtml), 'YO!info tmux sub-window rows render through the same tmux-window-button shell as the Info Bar');
    assert.ok(pathOnlyHtml.includes('<span class="info-tree-field-label">Tab(tmux session):</span>') && pathOnlyHtml.includes('<span class="info-tree-field-label">tmux sub-window:</span>'), 'YO!info leaf rows label Tab session and window actions');
    assert.ok(/info-tree-field-tab[\s\S]*session-button-name session-button-identifier">\[tab-a\]<\/strong>[\s\S]*tab-inline-detail">App main PR full description<\/span>/.test(pathOnlyHtml), 'YO!info Tab leaf values use the shared tab detail without repeating the PR badge');
    assert.ok(/info-tree-field-branch[\s\S]*?info-tree-meta-updated/.test(pathOnlyHtml) && !pathOnlyHtml.includes('<span class="info-tree-field-label">updated:</span>'), 'YO!info leaf rows attach branch recency to the Git branch instead of rendering a detached Updated row');
    const pathBranchHtml = api.infoTreeHtmlForTest(records, ['path', 'branch']);
    assert.equal((pathBranchHtml.match(/info-tree-field-path|info-tree-field-branch/g) || []).length, 0, 'YO!info hides every identity row already supplied by ancestor groups');
    assert.ok(pathBranchHtml.includes('<span class="info-tree-group-dimension">Git branch:</span>'), 'YO!info Branch group headers render as Git branch');
    assert.ok(/data-info-dimension="path"[\s\S]*\/repo\/app[\s\S]*info-tree-group-child-count">\(2 branches\)<\/span>/.test(pathBranchHtml), 'YO!info Path group headers show direct child branch counts inline after the path label');
    assert.equal(pathBranchHtml.includes('(1 branch)'), false, 'YO!info group headers omit inline child counts when there is only one child group');
    assert.equal(pathBranchHtml.includes('info-tree-group-count'), false, 'YO!info no longer renders detached right-side count bubbles');
    const tabPathHtml = api.infoTreeHtmlForTest(records, ['tab', 'path']);
    assert.ok(/data-info-dimension="tab"[\s\S]*<span class="info-tree-group-dimension">Tab\(tmux session\):<\/span>[\s\S]*tab-a/.test(tabPathHtml), 'YO!info Tab group headers render as Tab(tmux session): <value>');
    assert.ok(tabPathHtml.includes('info-tree-tab-token') && tabPathHtml.includes('tmux-pane-tab-token') && tabPathHtml.includes('pane-tab-core') && tabPathHtml.includes('data-info-tab-state='), 'YO!info Tab group headers render through the shared compact tmux pane-tab token');
    assert.ok(/data-info-dimension="tab"[\s\S]*session-button-name session-button-identifier">\[tab-a\]<\/strong>[\s\S]*tab-inline-detail">App main PR full description<\/span>/.test(tabPathHtml), 'YO!info Tab group headers use the same shared tab detail without repeating the PR badge');
    assert.equal((tabPathHtml.match(/info-tree-field-tab/g) || []).length, 0, 'YO!info hides Tab rows when Tab is already supplied by an ancestor group');
    const numericTabRecord = {...appMainRecord, id: 'numeric-tab-record', tabKey: '1', tabLabel: '1', tabTitle: '1', tabSession: '1'};
    api.setPinnedTabsForTest(['1']);
    api.setAutoApproveStateForTest('1', {
      enabled: true,
      screen: {key: 'needs-input', text: 'waiting for input', signature: 'ask-1'},
      agent_windows: [{kind: 'claude', state: 'working', window_index: 0, window_label: '0:claude', current: true, window_active: true}],
    });
    const numericTabHtml = api.infoTreeHtmlForTest([numericTabRecord], ['tab']);
    const numericTabMarkerHtml = numericTabHtml.match(/<span class="session-agent-activity-marker[^"]*">[\s\S]*?<\/span><\/span>/)?.[0] || '';
    assert.ok(numericTabHtml.includes('pane-tab-pin-icon'), 'YO!info Tab(tmux session) token shows the shared pinned-tab icon when the session is pinned');
    assert.ok(/session-yolo-marker[^"]*active[\s\S]*data-auto-session="1"/.test(numericTabHtml), 'YO!info Tab(tmux session) token shows the shared YO button state');
    assert.ok(/session-button-number session-button-identifier">\[1\]<\/strong>/.test(numericTabHtml), 'YO!info Tab(tmux session) token shows the same bold bracketed numeric session label as the real tab');
    assert.equal(/session-state-badge/.test(numericTabHtml), false, 'YO!info Tab(tmux session) token omits redundant text badges when the session needs input');
    assert.ok(/session-agent-activity-marker[\s\S]*agent-window-activity--status-only[\s\S]*agent-window-status-dot[\s\S]*status-indicator--working/.test(numericTabMarkerHtml), 'YO!info Tab(tmux session) token shows the shared colored working status ball');
    assert.equal(numericTabMarkerHtml.includes('agent-icon'), false, 'YO!info Tab(tmux session) token omits the Claude/Codex icon');
    api.setPinnedTabsForTest([]);
    const statusPriorityRecords = [
      {tabKey: 'red-tab', tabLabel: 'red-tab', tabSession: 'red-tab', aiKey: '0:codex', aiLabel: '0:codex', aiKind: 'codex', aiWindow: '0', aiState: 'working'},
      {tabKey: 'red-tab', tabLabel: 'red-tab', tabSession: 'red-tab', aiKey: '1:claude', aiLabel: '1:claude', aiKind: 'claude', aiWindow: '1', aiState: 'needs-input'},
      {tabKey: 'yellow-tab', tabLabel: 'yellow-tab', tabSession: 'yellow-tab', aiKey: '0:codex', aiLabel: '0:codex', aiKind: 'codex', aiWindow: '0', aiState: 'working'},
      {tabKey: 'yellow-tab', tabLabel: 'yellow-tab', tabSession: 'yellow-tab', aiKey: '1:claude', aiLabel: '1:claude', aiKind: 'claude', aiWindow: '1', aiState: 'idle', aiWorkingStoppedTs: Math.floor(Date.now() / 1000)},
      {tabKey: 'green-tab', tabLabel: 'green-tab', tabSession: 'green-tab', aiKey: '0:codex', aiLabel: '0:codex', aiKind: 'codex', aiWindow: '0', aiState: 'working'},
    ];
    const statusPriorityHtml = api.infoTreeHtmlForTest(statusPriorityRecords, ['tab'], {key: 'tab', dir: 'asc'});
    const tabSummaryFor = label => {
      const index = statusPriorityHtml.indexOf(`>[${label}]</`);
      const start = statusPriorityHtml.lastIndexOf('<summary', index);
      const end = statusPriorityHtml.indexOf('</summary>', index);
      return start >= 0 && end >= 0 ? statusPriorityHtml.slice(start, end) : '';
    };
    const redTabSummary = tabSummaryFor('red-tab');
    const yellowTabSummary = tabSummaryFor('yellow-tab');
    const greenTabSummary = tabSummaryFor('green-tab');
    assert.ok(/tmux-pane-tab-token[\s\S]*info-tree-tab-group-status[\s\S]*status-indicator--attention/.test(redTabSummary), 'YO!info Tab group aggregates red attention inside the shared tab token above other child tmux sub-window states');
    assert.ok(/tmux-pane-tab-token[\s\S]*info-tree-tab-group-status[\s\S]*status-indicator--cooldown/.test(yellowTabSummary) && !yellowTabSummary.includes('status-indicator--working'), 'YO!info Tab group aggregates yellow cooldown inside the shared tab token above green working child states');
    assert.ok(/tmux-pane-tab-token[\s\S]*info-tree-tab-group-status[\s\S]*status-indicator--working/.test(greenTabSummary), 'YO!info Tab group shows green inside the shared tab token when all child tmux sub-windows are working');
    assert.ok(greenTabSummary.includes('agent-window-status-dot--transition-glow'), 'YO!info inherits the same animated green status marker as the Tab surface');
    assert.equal((greenTabSummary.match(/\bagent-window-status-dot(?=\s|")/g) || []).length, 1, 'YO!info Tab group summaries do not render a duplicate standalone status dot next to the tab token');
    const noOwnerRecord = {
      id: 'orphan',
      tabKey: '__no_tab__',
      tabLabel: 'No tab',
      tabTitle: 'No tab or AI associated with this branch',
      aiKey: 'no-ai::No AI',
      aiLabel: 'No AI',
      aiTitle: 'No tab or AI associated with this branch',
      pathKey: '/repo/orphan',
      pathLabel: '/repo/orphan',
      pathTitle: '/repo/orphan',
      branchKey: 'orphan-branch',
      branchLabel: 'orphan-branch',
      branchTitle: 'orphan-branch',
      branchHtml: 'orphan-branch',
      prKey: '__no_pr__',
      prLabel: 'No PR',
      prTitle: 'No PR',
      linearKey: '__no_linear__',
      linearLabel: 'No Linear',
      linearTitle: 'No Linear',
      desc: '',
      updated: '',
      updatedTitle: '',
      updatedTs: 0,
    };
    const noOwnerMain = api.infoRecordHtmlForTest(noOwnerRecord);
    assert.ok(noOwnerMain.includes('/repo/orphan'), 'YO!info still shows a path in the record box when no ancestor group supplies it');
    assert.ok(noOwnerMain.includes('orphan-branch'), 'YO!info shows the local branch identity for otherwise unowned path rows');
    assert.equal(/No tab|No AI|No PR|No Linear/.test(noOwnerMain), false, 'YO!info leaf rows omit missing Tab, AI, PR, and Linear placeholders');
    const pathFreshnessRecord = {...noOwnerRecord, id: 'path-freshness', branchKey: '__no_branch__', branchLabel: 'No branch', branchTitle: 'No branch', updated: '4 days ago', updatedTitle: '4 days ago', updatedTs: 4, updatedSource: 'git-commit', pathActivityTs: Math.floor(Date.now() / 1000) - (5 * 60), pathActivitySource: 'dirty'};
    const pathFreshnessHtml = api.infoRecordHtmlForTest(pathFreshnessRecord);
    assert.ok(/info-tree-field-path[\s\S]*?info-tree-meta-path-activity info-tree-trailing-meta[\s\S]*?5 minutes ago/.test(pathFreshnessHtml) && pathFreshnessHtml.includes('title="Latest repository path activity: 5 minutes ago"') && !pathFreshnessHtml.includes('<span class="info-tree-field-label">updated:</span>'), 'YO!info gives Path its own shared trailing activity recency instead of reusing a Git commit date');
    const groupedPathFreshnessHtml = api.infoTreeHtmlForTest([pathFreshnessRecord], ['path']);
    assert.ok(/info-tree-group-label-path"[\s\S]*?<\/span><span class="info-tree-meta-updated info-tree-meta-path-activity info-tree-trailing-meta"[\s\S]*?5 minutes ago/.test(groupedPathFreshnessHtml), 'YO!info Path group headers move activity after the path label into the shared trailing metadata slot');
    const describedRecord = {
      id: 'described',
      tabKey: 'tab-a',
      tabSession: 'tab-a',
      tabLabel: 'tab-a',
      tabTitle: 'Open tab-a',
      aiKey: 'claude:2:2:claude',
      aiKind: 'claude',
      aiWindow: '2',
      aiLabel: '2:claude',
      aiTitle: 'Open tab-a window 2',
      aiPid: 2345,
      aiIdleSince: Math.floor(Date.now() / 1000) - (10 * 60),
      pathKey: '/repo/app',
      pathLabel: '/repo/app',
      pathTitle: '/repo/app',
      branchKey: 'main',
      branchLabel: 'main',
      branchTitle: 'main',
      branchHtml: 'main',
      prKey: '#12',
      prLabel: '#12',
      prTitle: '#12 PR title exists',
      prUrl: 'https://example.test/pull/12',
      linearKey: 'DYN-12',
      linearLabel: 'DYN-12',
      linearTitle: 'DYN-12 In Progress Linear title exists',
      linearItems: [{identifier: 'DYN-12', title: 'Linear title exists', url: 'https://linear.test/DYN-12'}],
      updated: '3 days ago',
      updatedTitle: '3 days ago',
      updatedTs: 12,
      updatedSource: 'git-commit',
    };
    const describedMain = api.infoRecordHtmlForTest(describedRecord, {hiddenDimensions: ['path']});
    assert.ok(!describedMain.includes('/repo/app') && describedMain.includes('<span class="info-tree-field-label">Git branch:</span>') && describedMain.includes('>main<') && describedMain.includes('<span class="info-tree-field-label">GitHub PR:</span>') && describedMain.includes('#12') && describedMain.includes('PR title exists') && describedMain.includes('<span class="info-tree-field-label">Linear:</span>') && describedMain.includes('DYN-12') && describedMain.includes('Linear title exists') && describedMain.includes('data-info-open-tab="tab-a"') && describedMain.includes('data-info-open-ai-window="2"') && describedMain.includes('3 days ago'), 'YO!info leaf rows contain only requested labeled fields, with ancestor path suppressed and visible branch identity');
    assert.ok(describedMain.indexOf('info-tree-field-tab') < describedMain.indexOf('info-tree-field-ai'), 'YO!info leaf rows list the Tab before the tmux sub-window');
    assert.ok(describedMain.indexOf('info-tree-field-ai') < describedMain.indexOf('info-tree-field-branch'), 'YO!info leaf rows list the tmux sub-window before the Git branch');
    assert.ok(/info-tree-field-branch[\s\S]*?>main<[\s\S]*?info-tree-meta-updated[\s\S]*?Git commit 3 days ago/.test(describedMain) && describedMain.includes('title="Git commit: 3 days ago"') && !describedMain.includes('<span class="info-tree-field-label">Updated:</span>'), 'YO!info identifies the Git commit date beside its Git branch instead of rendering a detached Updated row');
    assert.ok(/info-tree-ai-window-token[\s\S]*?info-tree-ai-pid">\(pid=2345\)<\/span>[\s\S]*?info-tree-ai-recency info-tree-trailing-meta">10 min ago<\/span>/.test(describedMain), 'YO!info keeps the PID inline after its tmux sub-window button and the recency trailing');
    assert.ok(describedMain.indexOf('<span class="info-tree-field-label">GitHub PR:</span>') < describedMain.indexOf('<span class="info-tree-field-label">Linear:</span>'), 'YO!info leaf rows render PR before Linear');
    const fullCss = fs.readFileSync('static/yolomux.css', 'utf8');
    const infoTreeCss = fs.readFileSync('static_src/css/yolomux/50_terminal_file_tree.css', 'utf8');
    const tokenCss = fs.readFileSync('static_src/css/yolomux/00_tokens_base.css', 'utf8');
    const paneTabCss = fs.readFileSync('static_src/css/yolomux/40_layout_panes_tabs.css', 'utf8');
	    const infoSource = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
	    const infoPanelSource = fs.readFileSync('static_src/js/yolomux/80_info_panel.js', 'utf8');
	    assert.ok(/function infoFieldLabel\(kind\)[\s\S]*path:\s*'info\.field\.path'[\s\S]*branch:\s*'info\.field\.gitBranch'[\s\S]*pr:\s*'info\.field\.githubPr'[\s\S]*'tmux-window':\s*'info\.field\.tmuxSubWindow'[\s\S]*return t\(labels\[kind\] \|\| kind\)/.test(infoSource), 'YO!info field and group labels route through one localized label owner');
	    assert.ok(/function infoGroupDimensionLabel\(key\)[\s\S]*infoFieldLabel\(key\)/.test(infoSource), 'YO!info group dimension labels reuse the same localized field label owner as leaf rows');
	    assert.ok(/\.info-tree-group\[data-info-dimension="tmux-window"\] > summary \.info-tree-group-dimension\s*\{[\s\S]*text-transform:\s*none/.test(infoTreeCss), 'YO!info tmux sub-window group labels preserve lowercase text');
	    assert.ok(/\.info-tree-group\[data-info-dimension="branch"\] > summary \.info-tree-group-dimension\s*\{[\s\S]*text-transform:\s*none/.test(infoTreeCss), 'YO!info Git branch group labels preserve the mixed-case Git prefix');
	    assert.ok(/\.info-tree-group\[data-info-dimension="pr"\] > summary \.info-tree-group-dimension\s*\{[\s\S]*text-transform:\s*none/.test(infoTreeCss), 'YO!info GitHub PR group labels preserve the mixed-case GitHub prefix');
    const longPath = '/home/test/dynamo/dynamo2/packages/frontend/src/really/deep/path/that/must/not/be/chopped/off';
    const longBranch = 'keivenchang/DIS-1200__complete-visible-branch-identity-that-must-not-be-chopped-off';
    const longPr = '#1200 This is the complete PR description and it must wrap instead of being truncated';
    const longLinear = 'DYN-1200 This is the complete Linear title and it must wrap instead of being truncated';
    const longRecord = {
      id: 'long',
      tabKey: 'tab-long',
      tabSession: 'tab-long',
      tabLabel: 'tab-long',
      tabTitle: 'Open tab-long',
      aiKey: 'codex:4:4:codex',
      aiKind: 'codex',
      aiWindow: '4',
      aiLabel: '4:codex',
      aiTitle: 'Open tab-long window 4',
      pathKey: longPath,
      pathLabel: '~/dynamo/dynamo2/packages/frontend/src/really/deep/path/that/must/not/be/chopped/off',
      pathTitle: longPath,
      branchKey: longBranch,
      branchLabel: longBranch,
      branchTitle: longBranch,
      branchHtml: longBranch,
      prKey: '#1200',
      prLabel: '#1200',
      prTitle: longPr,
      prUrl: 'https://example.test/pull/1200',
      linearKey: 'DYN-1200',
      linearLabel: 'DYN-1200',
      linearTitle: longLinear,
      linearItems: [{identifier: 'DYN-1200', title: 'This is the complete Linear title and it must wrap instead of being truncated', url: 'https://linear.test/DYN-1200'}],
      updated: '4 days ago',
      updatedTitle: '4 days ago',
      updatedTs: 12,
    };
    const longHtml = api.infoTreeHtmlForTest([longRecord], ['ai']);
    assert.ok(longHtml.includes(longPath) && longHtml.includes(longBranch) && longHtml.includes(longPr) && longHtml.includes(longLinear), 'YO!info leaf rows render full path, Branch, and full PR/Linear descriptions');
    assert.ok(longHtml.includes(`data-info-open-path="${longPath}"`), 'YO!info path rows are clickable Finder targets');
    assert.ok(/data-info-dimension="path"[\s\S]*data-info-open-path="\/home\/test\/dynamo\/dynamo2\/packages\/frontend\/src\/really\/deep\/path\/that\/must\/not\/be\/chopped\/off"/.test(api.infoTreeHtmlForTest([longRecord], ['path', 'branch'])), 'YO!info Path group labels are clickable Finder targets');
    assert.ok(/<span class="info-tree-field-label">GitHub PR:<\/span>[\s\S]*<a href="https:\/\/example\.test\/pull\/1200"[\s\S]*title="https:\/\/example\.test\/pull\/1200"[\s\S]*>#1200<\/a>[\s\S]*This is the complete PR description/.test(longHtml), 'YO!info PR rows make the PR number clickable and expose the full URL on hover');
    assert.ok(/<span class="info-tree-field-label">Linear:<\/span>[\s\S]*<a href="https:\/\/linear\.test\/DYN-1200"[\s\S]*title="https:\/\/linear\.test\/DYN-1200"[\s\S]*>DYN-1200<\/a>[\s\S]*This is the complete Linear title/.test(longHtml), 'YO!info Linear rows make the Linear identifier clickable and expose the full URL on hover');
    const longGroupTree = api.infoGroupTree([longRecord], ['path', 'pr', 'linear']);
    assert.equal(longGroupTree.children[0]?.label, longPath, 'YO!info Path group labels use the full path because descendant boxes suppress parent path metadata');
    assert.equal(longGroupTree.children[0]?.children[0]?.label, longPr, 'YO!info PR group labels use the full PR description because descendant boxes suppress parent PR metadata');
    assert.equal(longGroupTree.children[0]?.children[0]?.children[0]?.label, longLinear, 'YO!info Linear group labels use the full Linear title because descendant boxes suppress parent Linear metadata');
    assert.equal(api.infoGroupTree([longRecord], ['branch']).children[0]?.label, longBranch, 'YO!info Branch group labels use the full local branch name because descendant boxes suppress parent branch metadata');
    assert.ok(/\.info-tree-record-main\s*\{[\s\S]*flex-direction:\s*column[\s\S]*padding-inline-start:\s*10px/.test(infoTreeCss), 'YO!info record fields stack as one left-indented labeled row per line');
    assert.ok(/\.info-tree-record\s*\{[\s\S]*--info-tree-field-label-width:\s*18ch/.test(infoTreeCss), 'YO!info records define one shared label column width for aligned key/value rows');
    assert.equal(infoTreeCss.includes('.info-tree-list::before'), false, 'YO!info does not reserve a fixed-height sticky mask that leaves blank space under a single sticky parent');
    assert.ok(/\.info-tree-panel \.info-pane\.info-tree-pane-scrolled::before\s*\{[\s\S]*position:\s*absolute[\s\S]*z-index:\s*2[\s\S]*height:\s*var\(--info-tree-sticky-level-block,\s*27px\)[\s\S]*background:\s*var\(--info-pane-bg\)[\s\S]*pointer-events:\s*none/.test(infoTreeCss), 'YO!info masks clipped leaf rows only after the tree body scrolls, without adding layout space');
    assert.ok(/\.info-tree\s*\{[\s\S]*--info-tree-children-gap:\s*0px/.test(infoTreeCss), 'YO!info sibling leaf node boxes touch vertically without an inserted gap');
    assert.ok(/--info-tree-record-border:\s*var\(--info-tree-line\)/.test(tokenCss), 'YO!info leaf box outline uses the same visible color token as the left tree guides');
    assert.ok(/\.info-tree-record\s*\{[\s\S]*background:\s*transparent[\s\S]*border:\s*1px solid var\(--info-tree-record-border\)[\s\S]*border-radius:\s*8px[\s\S]*box-shadow:\s*none/.test(infoTreeCss), 'YO!info leaf rows keep transparent fill with only a faint 1px rounded node outline and no card shadow');
    assert.ok(/\.info-tree-group-children > \.info-tree-record\s*\{[\s\S]*border-radius:\s*0[\s\S]*\.info-tree-group-children > \.info-tree-record:not\(\.info-tree-item-first\)\s*\{[\s\S]*border-block-start-width:\s*0[\s\S]*\.info-tree-group-children > \.info-tree-record\.info-tree-item-first\s*\{[\s\S]*border-start-start-radius:\s*8px[\s\S]*border-start-end-radius:\s*8px[\s\S]*\.info-tree-group-children > \.info-tree-record\.info-tree-item-last\s*\{[\s\S]*border-end-start-radius:\s*8px[\s\S]*border-end-end-radius:\s*8px/.test(infoTreeCss), 'YO!info sibling leaf rows share four outer rounded corners with one straight internal divider');
    assert.ok(/\.info-tree-field\s*\{[\s\S]*grid-template-columns:\s*var\(--info-tree-field-label-width\) minmax\(0, 1fr\)[\s\S]*width:\s*100%/.test(infoTreeCss), 'YO!info labeled rows keep aligned labels and wrapping values');
    assert.ok(/\.info-tree-field-label\s*\{[\s\S]*justify-self:\s*start[\s\S]*text-align:\s*left/.test(infoTreeCss), 'YO!info field labels left-align while every value starts in the same column');
    assert.ok(/\.info-tree-field,[\s\S]*\.info-tree-field-value,[\s\S]*\.info-tree-value-text\s*\{[\s\S]*overflow:\s*visible[\s\S]*text-overflow:\s*clip[\s\S]*white-space:\s*normal[\s\S]*overflow-wrap:\s*anywhere/.test(infoTreeCss), 'YO!info row values wrap instead of ellipsizing');
    assert.ok(/\.info-tree-group\[data-info-dimension="path"\][\s\S]*\.info-tree-group\[data-info-dimension="branch"\][\s\S]*\.info-tree-group\[data-info-dimension="pr"\][\s\S]*\.info-tree-group\[data-info-dimension="linear"\][\s\S]*summary \.info-tree-group-label\s*\{[\s\S]*overflow:\s*visible[\s\S]*text-overflow:\s*clip[\s\S]*white-space:\s*normal[\s\S]*overflow-wrap:\s*anywhere/.test(infoTreeCss), 'YO!info Path/Branch/PR/Linear group labels wrap instead of ellipsizing when they own ancestor metadata');
    assert.ok(/\.info-tree-group-children > \.info-tree-item::after\s*\{[\s\S]*inset-block-start:\s*calc\(\(var\(--info-tree-children-gap\) \/ -2\) - var\(--info-tree-connector-line-width\)\)[\s\S]*inset-block-end:\s*calc\(\(var\(--info-tree-children-gap\) \/ -2\) - var\(--info-tree-connector-line-width\)\)/.test(infoTreeCss), 'YO!info vertical tree guides overlap adjacent leaf boxes by one line width so the left guide stays continuous');
    assert.ok(/\.info-tree-group-children > \.info-tree-item-last::after\s*\{[\s\S]*height:\s*calc\(var\(--info-tree-record-connector-y\) \+ \(var\(--info-tree-connector-line-width\) \* 2\) \+ \(var\(--info-tree-children-gap\) \/ 2\)\)/.test(infoTreeCss) && /\.info-tree-group-children > \.info-tree-group\.info-tree-item-last::after\s*\{[\s\S]*height:\s*calc\(var\(--info-tree-summary-connector-y\) \+ \(var\(--info-tree-connector-line-width\) \* 2\) \+ \(var\(--info-tree-children-gap\) \/ 2\)\)/.test(infoTreeCss), 'YO!info last-child connectors stop at the row-specific horizontal arm to form a 90-degree angle');
    assert.ok(/\.info-tree\s*\{[\s\S]*--info-tree-sticky-level-block:\s*27px[\s\S]*--info-tree-connector-line-width:\s*1px[\s\S]*--info-tree-connector-arm-start:\s*calc\(var\(--info-tree-connector-x\) \+ var\(--info-tree-connector-line-width\)\)[\s\S]*--info-tree-summary-connector-y:\s*13px[\s\S]*--info-tree-record-connector-y:\s*13px/.test(infoTreeCss), 'YO!info defines compact sticky rows with connector arms aligned to the text midline and offset from the vertical stroke');
    assert.ok(/\.info-tree-group summary\s*\{[\s\S]*position:\s*sticky[\s\S]*grid-template-columns:\s*max-content max-content minmax\(0, 1fr\)[\s\S]*align-items:\s*center[\s\S]*align-content:\s*center[\s\S]*padding:\s*2px 4px[\s\S]*background:\s*var\(--info-pane-bg\)[\s\S]*border:\s*0[\s\S]*box-shadow:\s*none/.test(infoTreeCss), 'YO!info sticky parent headers are compact vertically-centered text rows with inline labels instead of boxed cards or right count pills');
    const dimensionSummaryBlocks = [...infoTreeCss.matchAll(/\.info-tree-group\[data-info-dimension="(?:path|branch|pr|linear)"\] > summary\s*\{[\s\S]*?\}/g)].map(match => match[0]).join('\n');
    assert.equal(dimensionSummaryBlocks.includes('align-items: start'), false, 'YO!info dimension group summaries do not override vertical centering');
    assert.ok(/\.info-tree-group\[data-info-depth="1"\] > summary::after,[\s\S]*\.info-tree-group\[data-info-depth="3"\] > summary::after\s*\{[\s\S]*inset-block-start:\s*var\(--info-tree-summary-connector-y\)[\s\S]*inset-inline-start:\s*var\(--info-tree-connector-arm-start\)[\s\S]*width:\s*var\(--info-tree-connector-arm-width\)[\s\S]*background:\s*var\(--info-tree-line\)/.test(infoTreeCss), 'YO!info sticky parent summaries draw one horizontal connector arm aligned to the summary label row without overpainting the vertical stroke');
    assert.ok(/\.info-tree-group-children > \.info-tree-group\.info-tree-item::before\s*\{[\s\S]*content:\s*none/.test(infoTreeCss), 'YO!info group rows do not draw a second child-row horizontal connector on top of the sticky summary connector');
    assert.ok(/\.info-tree\s*\{[\s\S]*--info-tree-tab-color:\s*var\(--pane-tab-active-bg\)[\s\S]*--info-tree-ai-color:\s*var\(--icon-code\)[\s\S]*--info-tree-path-color:\s*var\(--text\)[\s\S]*--info-tree-branch-color:\s*var\(--link-soft\)[\s\S]*--info-tree-pr-color:\s*var\(--info-tree-pr-neutral\)[\s\S]*--info-tree-linear-color:\s*#5eead4/.test(infoTreeCss), 'YO!info dark-mode Path uses normal text color and Branch uses blue while Tab stays tied to the theme color');
    assert.ok(/body\.theme-light \.info-tree\s*\{[\s\S]*--info-tree-ai-color:\s*var\(--icon-code\)[\s\S]*--info-tree-path-color:\s*var\(--text\)[\s\S]*--info-tree-branch-color:\s*var\(--link-soft\)[\s\S]*--info-tree-pr-color:\s*var\(--info-tree-pr-neutral\)[\s\S]*--info-tree-linear-color:\s*var\(--git-untracked-badge\)/.test(infoTreeCss), 'YO!info light-mode Path uses black text and Branch uses blue without using red or merged purple for PR/Branch');
    assert.equal(/--info-tree-branch-color:\s*(?:var\(--code-keyword\)|#6d28d9|#5b21b6)/.test(infoTreeCss), false, 'YO!info Branch color does not use the purple reserved for merged PR status');
    assert.equal(/--info-tree-pr-color:\s*(?:#fb7185|#fecdd3|#be123c|#9f1239)/.test(infoTreeCss), false, 'YO!info PR relationship color does not use the red reserved for status/attention');
    assert.ok(/:root\s*\{[\s\S]*--info-tree-pr-neutral:\s*var\(--link-soft-hover\)[\s\S]*--info-tree-pr-neutral-hover:\s*var\(--text\)/.test(tokenCss), 'YO!info dark-mode PR relationship labels use readable link-toned text instead of muted neutral gray');
    assert.ok(/body\.theme-light\s*\{[\s\S]*--info-tree-pr-neutral:\s*var\(--link-soft\)[\s\S]*--info-tree-pr-neutral-hover:\s*var\(--link-soft-hover\)/.test(tokenCss), 'YO!info light-mode PR relationship labels use readable link-toned text instead of muted neutral gray');
    assert.ok(/\.info-tree-group\[data-info-dimension="tab"\] > summary\s*\{[\s\S]*--info-tree-dimension-color:\s*var\(--info-tree-tab-color\)/.test(infoTreeCss) && /\.info-tree-group\[data-info-dimension="linear"\] > summary\s*\{[\s\S]*--info-tree-dimension-color:\s*var\(--info-tree-linear-color\)/.test(infoTreeCss), 'YO!info group headers route each dimension through its own color token');
    assert.ok(/\.info-tree-field-tab\s*\{[\s\S]*--info-tree-field-color:\s*var\(--info-tree-tab-color\)[\s\S]*\.info-tree-field-linear\s*\{[\s\S]*--info-tree-field-color:\s*var\(--info-tree-linear-color\)/.test(infoTreeCss), 'YO!info record rows route each dimension through its own color token');
    assert.ok(/\.info-tree-field-ai\s*\{[\s\S]*--info-tree-field-color:\s*var\(--pane-tab-active-bg\)[\s\S]*--info-tree-field-hover:\s*var\(--pane-tab-active-border\)/.test(infoTreeCss), 'YO!info tmux sub-window leaf rows follow the active theme color');
    assert.ok(/\.tmux-pane-tab-token\s*\{[\s\S]*background:\s*var\(--pane-inactive-tab-bg\)[\s\S]*border-radius:\s*var\(--pane-tab-top-radius\) var\(--pane-tab-top-radius\) 0 0[\s\S]*font-size:\s*var\(--tab-label-size\)/.test(paneTabCss) && /\.tmux-pane-tab-token\.active\s*\{[\s\S]*background:\s*var\(--pane-tab-active-bg\)/.test(paneTabCss), 'shared compact tmux pane-tab tokens own inactive and active tab styling');
    assert.ok(/function sessionShouldOfferYoloMarker\(session, info, payload, auto, state = null\)[\s\S]*autoApproveEnabledElsewhere\(payload\)[\s\S]*STATE_KEY\.needsApproval[\s\S]*STATE_KEY\.needsInput/.test(fs.readFileSync('static_src/js/yolomux/60_popovers_tabs.js', 'utf8')), 'tmux session tabs offer the YO button only for enabled, externally locked, or prompted sessions');
    assert.ok(/function tmuxPaneTabHtml\(session, info, state, auto, options = \{\}\)[\s\S]*sessionTabLeadingActivityHtml\(session, info, auto,[\s\S]*state/.test(fs.readFileSync('static_src/js/yolomux/78_panel_shell.js', 'utf8')), 'real tabs, Tabber, and YO!info pass the shared tab state into the shared YO marker offer rule');
    assert.ok(/function infoRecordTabValueHtml\(record = \{\}, options = \{\}\)[\s\S]*tmuxPaneTabTokenHtml\(record\.tabSession,[\s\S]*sessionLabelHtml,/.test(infoSource), 'YO!info Tab values delegate their content unchanged to the shared compact tmux pane-tab renderer');
    assert.equal(infoSource.includes('tabWorkDescription'), false, 'YO!info has no parallel Tab detail source that can duplicate the shared PR badge');
    assert.ok(/\.info-tree-tab-token\s*\{[\s\S]*inline-size:\s*100%[\s\S]*max-width:\s*100%/.test(infoTreeCss), 'YO!info Tab tokens fill their available group or leaf row so shared work text has the same responsive capacity as Tabber');
    assert.ok(/function infoGroupLabelHtml\(group = \{\}\)[\s\S]*leadingHtml:\s*infoTabGroupLeadingActivityHtml\(group\)/.test(infoSource), 'YO!info Tab group status is routed into the shared compact tmux pane-tab token instead of prepending a standalone dot');
    assert.equal(infoSource.includes('infoTabGroupStatusHtml(group)}${tabHtml'), false, 'YO!info Tab group summaries do not prepend a duplicate standalone status dot before the tab token');
    assert.equal(/function infoRecordTabValueHtml\(record = \{\}, options = \{\}\)[\s\S]*showLeading:\s*false|function infoRecordTabValueHtml\(record = \{\}, options = \{\}\)[\s\S]*showState:\s*false|function infoRecordTabValueHtml\(record = \{\}, options = \{\}\)[\s\S]*showBadges:\s*false/.test(infoSource), false, 'YO!info Tab values do not suppress the real tab YO marker, state badge path, or status indicators');
    assert.ok(/function tmuxPaneTabTokenHtml\(session, options = \{\}\)[\s\S]*tabIsPinned\(item\)[\s\S]*pinnedTabIconHtml\(item\)/.test(fs.readFileSync('static_src/js/yolomux/78_panel_shell.js', 'utf8')), 'shared compact tmux pane-tab tokens include the same pinned-tab icon helper as real tabs');
    assert.ok(/function infoRecordAiWindowButtonHtml\(record,[\s\S]*data-info-open-ai-window[\s\S]*tmuxWindowButtonHtml\(\{[\s\S]*classes:\s*\['info-tree-ai-window-button'\][\s\S]*title,/.test(infoSource) && !/function infoRecordAiWindowButtonHtml\(record,[\s\S]*activityAnimate:\s*false/.test(infoSource), 'YO!info AI values use the shared animated Info Bar button helper');
	    assert.ok(/\.info-tree-ai-value\.tmux-window-bar\s*\{[\s\S]*justify-content:\s*flex-start[\s\S]*\.info-tree-field-ai \.info-tree-ai-value\.tmux-window-bar\s*\{[\s\S]*flex:\s*1 1 auto[\s\S]*width:\s*100%/.test(infoTreeCss), 'YO!info sub-window rows stretch the shared button container so trailing metadata can align with Tabber dates');
	    assert.ok(/function pullRequestStatusBadgeHtml\(session, text, statusClass, options = \{\}\)[\s\S]*ci-indicator tab-symbol[\s\S]*function infoStatusBadgeHtml\(record, text, className, options = \{\}\)[\s\S]*pullRequestStatusBadgeHtml\(record\?\.tabSession/.test(`${fs.readFileSync('static_src/js/yolomux/70_layout_actions.js', 'utf8')}\n${infoSource}`), 'YO!info PR status labels reuse the same badge renderer as MAIN and tab DRAFT badges');
	    assert.ok(/\.info-tree-field-branch \.info-tree-field-value,[\s\S]*\.info-tree-field-ai \.info-tree-field-value\s*\{[\s\S]*display:\s*flex[\s\S]*\.info-tree-field-branch \.info-tree-meta-updated,[\s\S]*\.info-tree-field-ai \.info-tree-trailing-meta\s*\{[\s\S]*margin-inline-start:\s*auto/.test(infoTreeCss), 'YO!info branch and sub-window recency share the right-aligned trailing metadata parent');
	    assert.ok(/\.info-tree-group\[data-info-dimension="path"\] > summary \.info-tree-group-label-line\s*\{[\s\S]*display:\s*flex[\s\S]*gap:\s*6px[\s\S]*\.info-tree-group\[data-info-dimension="path"\] > summary \.info-tree-meta-path-activity,[\s\S]*\.info-tree-field-path \.info-tree-meta-path-activity\s*\{[\s\S]*margin-inline-start:\s*auto[\s\S]*text-align:\s*end/.test(infoTreeCss), 'YO!info Path activity uses the same right-aligned trailing metadata geometry in group and leaf rows');
	    assert.ok(/\.info-tree-trailing-meta\s*\{[\s\S]*color:\s*var\(--subwindow-recency-color\)[\s\S]*font:\s*var\(--ui-font-size-2xs\)\/1 var\(--mono-font\)/.test(infoTreeCss), 'YO!info Git commit and sub-window recency inherit one shared trailing-metadata style');
	    assert.ok(/function infoRecordAiValueHtml\(record, options = \{\}\) \{[\s\S]*\$\{buttonHtml\}\$\{status\}\$\{pid\}\$\{recency\}/.test(infoSource), 'YO!info places PID inline after the button/status and keeps recency trailing');
	    assert.ok(/\.info-tree-ai-pid\s*\{[\s\S]*flex:\s*0 0 auto[\s\S]*color:\s*var\(--subwindow-pid-color\)/.test(infoTreeCss), 'YO!info PID stays inline with its muted shared metadata color');
	    assert.ok(/:root\s*\{[\s\S]*--subwindow-pid-color:\s*var\(--text-muted-soft\)[\s\S]*--subwindow-recency-color:\s*var\(--text-muted-soft\)[\s\S]*body\.theme-light\s*\{[\s\S]*--subwindow-pid-color:\s*var\(--text-muted-cool\)[\s\S]*--subwindow-recency-color:\s*var\(--text-muted-cool\)/.test(tokenCss), 'sub-window PID and recency use the brighter shared muted metadata color in light mode without changing dark mode');
	    assert.ok(/function infoRecordAiRecencyHtml\(record\)[\s\S]*function infoRecordAiPidHtml\(record\)[\s\S]*tmuxWindowPidText\(record\?\.aiPid\)/.test(infoSource), 'YO!info reuses the shared PID formatter and existing recency formatter');
    assert.ok(/\.info-tree-group-child-count\s*\{[\s\S]*margin-inline-start:\s*6px[\s\S]*color:\s*var\(--muted\)/.test(infoTreeCss), 'YO!info child counts render inline beside group labels in a less prominent color');
    assert.equal(infoTreeCss.includes('.info-tree-group-count'), false, 'YO!info CSS no longer defines the detached count bubble');
    assert.ok(/\.info-tree-group\[data-info-depth="0"\]\s*>\s*summary\s*\{[\s\S]*inset-block-start:\s*0[\s\S]*z-index:\s*4[\s\S]*\.info-tree-group\[data-info-depth="1"\]\s*>\s*summary\s*\{[\s\S]*inset-block-start:\s*var\(--info-tree-sticky-level-block\)[\s\S]*z-index:\s*5[\s\S]*\.info-tree-group\[data-info-depth="3"\]\s*>\s*summary\s*\{[\s\S]*inset-block-start:\s*calc\(var\(--info-tree-sticky-level-block\) \* 3\)/.test(infoTreeCss), 'YO!info sticky parent headers stack by tree depth instead of piling on top of each other');
    assert.ok(/\.ui-disclosure-triangle,[\s\S]*\.info-tree-group summary::before,[\s\S]*\.yoagent-message-details summary::before\s*\{[\s\S]*--disclosure-triangle-box-size:\s*1\.333333em[\s\S]*--disclosure-triangle-font-size:\s*100%[\s\S]*inline-size:\s*var\(--disclosure-triangle-box-size\)[\s\S]*color:\s*var\(--disclosure-triangle-collapsed-color\)[\s\S]*font-size:\s*var\(--disclosure-triangle-font-size\)/.test(fullCss), 'disclosure chevrons share one scaled muted collapsed parent style');
    assert.ok(/\.info-tree-group summary::before\s*\{\s*content:\s*"›";\s*\}[\s\S]*\.info-tree-group:not\(\[open\]\) summary::before\s*\{\s*content:\s*"›";\s*\}/.test(infoTreeCss), 'YO!info disclosure chevrons use one shared rotated glyph without a bespoke larger font');
    assert.ok(/\.info-actions-bar\s*\{[\s\S]*position:\s*relative[\s\S]*z-index:\s*var\(--z-layer-marker\)[\s\S]*background:\s*var\(--pane-bar-bg/.test(infoTreeCss), 'YO!info action bar paints as the opaque layer above sticky tree summaries');
    assert.ok(/\.info-tree-actions-bar\s*\{[\s\S]*flex-wrap:\s*wrap[\s\S]*row-gap:\s*5px/.test(infoTreeCss), 'YO!info toolbar allows a two-line control layout');
    assert.ok(/\.info-tree-primary-controls\s*\{[\s\S]*flex:\s*1 0 100%/.test(infoTreeCss), 'YO!info grouping presets and search occupy the first toolbar line above the order-by chain');
    assert.ok(/\.info-tree-search-control\s*\{[\s\S]*min-width:\s*0[\s\S]*max-width:\s*100%[\s\S]*flex:\s*1 1 180px/.test(infoTreeCss), 'YO!info search input shrinks with the pane instead of forcing a wide toolbar');
    assert.ok(/\.info-tree-order-label,[\s\S]*\.info-tree-order-separator\s*\{[\s\S]*color:\s*var\(--muted\)[\s\S]*white-space:\s*nowrap/.test(infoTreeCss), 'YO!info order-by label and separators share compact muted toolbar styling');
    assert.ok(/\.info-tree-search-control input\s*\{[\s\S]*height:\s*24px/.test(infoTreeCss), 'YO!info toolbar includes a compact search input');
    assert.ok(/\.info-tree-search-match\s*\{[\s\S]*color:\s*var\(--info-tree-search-match-text\)[\s\S]*background:\s*var\(--info-tree-search-match-bg\)/.test(infoTreeCss), 'YO!info search matches have a dedicated non-red/non-purple highlight style');
    assert.ok(/\.info-tree-sort-controls\s*\{[\s\S]*margin-inline-start:\s*auto[\s\S]*justify-content:\s*flex-end/.test(infoTreeCss), 'YO!info Sort control is right-aligned on the selector toolbar line');
    assert.ok(/delegate\(panel, 'click', '\[data-auto-session\]\[data-action="pane-tab-auto-approve"\]'[\s\S]*toggleAutoApprove\(button\.dataset\.autoSession/.test(infoPanelSource), 'YO!info Tab(tmux session) YO marker clicks toggle YO before the surrounding tab-open handler runs');
    assert.ok(infoPanelSource.includes('data-info-search') && infoPanelSource.includes('setInfoSearch'), 'YO!info toolbar exposes a search box that filters relationship records');
    assert.ok(infoPanelSource.includes('data-info-refresh title="${esc(t(\'meta.refresh\'))}"') && infoPanelSource.includes('setMetadataRefreshButtonLoading(refresh, transcriptMetaLoading, t(\'meta.refresh\'), t(\'meta.refresh\'))'), 'YO!info metadata refresh button uses the compact Refresh label');
    assert.ok(/function setMetadataRefreshButtonLoading\(button, loading, idleLabel, idleTitle\)[\s\S]*button\.textContent = idleLabel;/.test(infoSource) && !/function setMetadataRefreshButtonLoading\(button, loading, idleLabel, idleTitle\)[\s\S]*button\.textContent = loading \?/.test(infoSource), 'YO!info metadata loading keeps the Refresh button label footprint stable');
    assert.ok(infoPanelSource.includes("info-tree-order-label\">${esc(t('info.group.orderBy'))}</span>") && infoPanelSource.includes('info-tree-order-separator') && infoPanelSource.includes('&gt;'), 'YO!info grouping controls render their localized Order by: label before select > select > select > select');
    assert.equal(/<span>\$\{index \+ 1\}<\/span>/.test(infoPanelSource), false, 'YO!info grouping controls do not render numeric 1/2/3/4 labels');
    assert.ok(infoPanelSource.includes('data-info-sort-mode') && !infoPanelSource.includes('data-info-sort-key') && !infoPanelSource.includes('data-info-sort-dir'), 'YO!info toolbar exposes one sort-mode select instead of separate Sort and Dir selects');
    assert.ok(/delegate\(panel, 'click', '\[data-info-open-path\]'[\s\S]*openFileExplorerPane[\s\S]*openFileExplorerAt\(path, \{manualSelection: true\}\)/.test(infoPanelSource), 'YO!info path clicks open Finder at the clicked path');
    assert.deepStrictEqual(canonical(api.infoSortFields().map(field => `${field.value}:${field.label}`)), ['name:asc:A-Z', 'name:desc:Z-A', 'date:desc:recent', 'date:asc:oldest'], 'YO!info exposes only A-Z, Z-A, recent, and oldest sort modes');
    api.setInfoSortForTest('name:desc');
    assert.deepStrictEqual(canonical(api.currentInfoSortForTest()), {dir: 'desc', key: 'name'}, 'YO!info stores the selected sort mode');
  });

  test('t@info-pr-sort-numeric', () => {
    const api = loadYolomux('', ['sort-pr']);
    api.setTranscriptInfoForTest('sort-pr', {
      project: {
        git: {
          root: '/repo/sort-pr',
          branch: 'small-pr',
          other_branches: {
            branches: [
              {name: 'large-pr', current: false, updated: 'today', updated_ts: 300, subject: 'large PR', pull_request: {number: 1111, title: 'large PR'}},
              {name: 'small-pr', current: true, updated: 'today', updated_ts: 200, subject: 'small PR', pull_request: {number: 9, title: 'something'}},
              {name: 'no-pr', current: false, updated: 'today', updated_ts: 100, subject: 'no PR'},
            ],
          },
        },
        pull_request: null,
        linear: [],
      },
    });

    const records = api.infoRelationshipRecords();
    assert.deepStrictEqual(canonical(api.infoGroupTree(records, ['pr'], {key: 'name', dir: 'asc'}).children.map(group => group.label)), [
      '#9 something',
      '#1111 large PR',
      'No PR',
    ], 'YO!info PR sort asc compares PR numbers instead of lexical labels');

    assert.deepStrictEqual(canonical(api.infoGroupTree(records, ['pr'], {key: 'name', dir: 'desc'}).children.map(group => group.label)), [
      '#1111 large PR',
      '#9 something',
      'No PR',
    ], 'YO!info PR sort desc compares PR numbers and keeps missing PRs last');
  });

  test('t@info-linear-sort-missing-after-z', () => {
    const api = loadYolomux('', ['sort-linear']);
    api.setTranscriptInfoForTest('sort-linear', {
      project: {
        git: {
          root: '/repo/sort-linear',
          branch: 'alpha-linear',
          other_branches: {
            branches: [
              {name: 'zeta-linear', current: false, updated: 'today', updated_ts: 300, subject: 'zeta Linear', linear: [{identifier: 'ZZZ-200', title: 'zeta issue'}]},
              {name: 'no-linear', current: false, updated: 'today', updated_ts: 200, subject: 'no Linear'},
              {name: 'alpha-linear', current: true, updated: 'today', updated_ts: 100, subject: 'alpha Linear', linear: [{identifier: 'AAA-100', title: 'alpha issue'}]},
            ],
          },
        },
        pull_request: null,
        linear: [],
      },
    });

    const records = api.infoRelationshipRecords();
    assert.deepStrictEqual(canonical(api.infoGroupTree(records, ['linear'], {key: 'name', dir: 'asc'}).children.map(group => group.label)), [
      'AAA-100 alpha issue',
      'ZZZ-200 zeta issue',
      'No Linear',
    ], 'YO!info Linear sort asc treats missing Linear as after z');

    assert.deepStrictEqual(canonical(api.infoGroupTree(records, ['linear'], {key: 'name', dir: 'desc'}).children.map(group => group.label)), [
      'ZZZ-200 zeta issue',
      'AAA-100 alpha issue',
      'No Linear',
    ], 'YO!info Linear sort desc keeps missing Linear after real Linear issues');
  });

  test('t@6833', () => {
    const api = loadYolomux('', ['alpha', 'beta'], 'http:', 'Linux x86_64', 'admin', {
      bootstrapOverrides: {
        availableAgents: ['codex', 'claude'],
        agentAuth: {
          codex: {installed: true, logged_in: true},
          claude: {installed: true, logged_in: true},
        },
      },
    });
    const baseActivitySummaryPayload = {
      generated_at: '2026-05-31T12:00:00+00:00',
      global: {
        headline: "Your most recent work is about editor fixes, and you are currently making changes to yolomux.dev in order to finish editor fixes. So far: 3 files changed (+9/-2); 1 of 2 AI agents is active.",
        lines: [
          "Your most recent work is about editor fixes, and you are currently making changes to yolomux.dev in order to finish editor fixes. So far: 3 files changed (+9/-2); 1 of 2 AI agents is active.",
          'Session alpha: Codex is active in yolomux.dev; 2 files changed (+8/-1); editor fixes',
        ],
      },
      sessions: {
        alpha: {local: "Codex session alpha is active in yolomux.dev. It has been working on editor fixes. It currently has 2 files changed (+8/-1)."},
      },
      agents: [
        {session: '5', window: '2', window_name: 'codex', window_label: '2:codex', agent_kind: 'codex', label: "session '5' 2:codex", running: true, sort_ts: Date.now() / 1000, cwd: '/home/test/yolomux.dev', recent_paths: [{path: '/home/test/yolomux.dev', mtime: Date.now() / 1000, count: 2}]},
        {session: '6', window: '1', window_name: 'claude', window_label: '1:claude', agent_kind: 'claude', label: "session '6' 1:claude", last_used_ts: Date.now() / 1000 - 180, sort_ts: Date.now() / 1000 - 180, cwd: '/home/test/other', recent_paths: [{path: '/home/test/other', mtime: Date.now() / 1000 - 180, count: 1}]},
      ],
    };
    const noAgentApi = loadYolomux('', ['alpha', 'beta']);
    noAgentApi.setActivitySummaryPayloadForTest(baseActivitySummaryPayload);
    const noAgentHtml = noAgentApi.yoagentChatHtml();
    assert.ok(noAgentHtml.includes('data-yoagent-chat-form'), 'No-backend YO!agent still shows a disabled chat form');
    assert.ok(noAgentHtml.includes('Set a Claude or Codex backend in Preferences to chat.'), 'No-backend YO!agent shows the disabled backend message');
    assert.ok(/data-yoagent-backend[\s\S]*disabled[\s\S]*No agent/.test(noAgentHtml), 'No-backend composer shows a disabled none backend state');
    const claudeOnlyApi = loadYolomux('', ['alpha', 'beta'], 'http:', 'Linux x86_64', 'admin', {
      bootstrapOverrides: {
        availableAgents: ['claude'],
        agentAuth: {claude: {installed: true, logged_in: true}},
      },
    });
    const claudeOnlyHtml = claudeOnlyApi.yoagentChatHtml();
    assert.ok(/data-yoagent-backend[\s\S]*<option value="claude" selected/.test(claudeOnlyHtml), 'Composer selects the only installed logged-in backend');
    assert.equal(/data-yoagent-backend[\s\S]*<option value="codex"/.test(claudeOnlyHtml), false, 'Composer hides unavailable backends');
    api.setActivitySummaryPayloadForTest(baseActivitySummaryPayload);
    assert.ok(api.globalActivitySummaryHtml().includes('YO!agent'), 'global activity summary uses the YO agent label');
    assert.equal(api.globalActivitySummaryHtml().includes('Session alpha'), false, 'YO!agent default panel does not expose the per-session SESSION detail list');
    api.setClientSettingsPatchForTest({yoagent: {backend: 'claude'}});
    assert.equal(api.yoagentChatHtml().includes('Your most recent work is about editor fixes'), false, 'Claude-backed YO!agent does not auto-inject Recent agents until the startup one-shot is enabled');
    assert.equal(api.showYoagentStartupInfoOnceForTest(), true, 'YO!agent startup info can be shown once when the tab first opens');
    assert.equal(api.showYoagentStartupInfoOnceForTest(), false, 'YO!agent startup info does not re-show on later renders');
    const enabledChatHtml = api.yoagentChatHtml();
    assert.ok(enabledChatHtml.includes('data-yoagent-chat-form'), 'Claude-backed YO!agent panel includes a chat form');
    assert.ok(enabledChatHtml.includes('Your most recent work is about editor fixes'), 'Claude-backed YO!agent chat shows the regular intro message only during startup');
    assert.ok(enabledChatHtml.includes('Ask anything'), 'Claude-backed YO!agent composer uses the localized ask-anything placeholder');
    assert.ok(enabledChatHtml.includes('yoagent-message assistant yoagent-recent-agents-message'), 'YO!agent chat shows recent agents as an assistant-style response during startup');
    assert.ok(enabledChatHtml.includes('<ul class="yoagent-recent-agents-list">'), 'YO!agent chat shows recent agents as a bullet list');
    assert.ok(enabledChatHtml.includes('yoagent-recent-agent-session">session 5'), 'YO!agent recent agents show the session in a fixed field');
    assert.ok(enabledChatHtml.includes('yoagent-recent-agent-window">2:codex'), 'YO!agent recent agents show the tmux sub-window name in a fixed field');
    assert.ok(enabledChatHtml.includes('yoagent-recent-agent-paths">~/yolomux.dev'), 'YO!agent recent agents show touched paths from the backend agent payload');
    assert.ok(enabledChatHtml.includes('yoagent-recent-agent-activity">running'), 'YO!agent recent agents show running agents as running');
    assert.ok(enabledChatHtml.includes('yoagent-recent-agent-activity">3 min ago'), 'YO!agent recent agents show compact last-used time for idle agents');
    assert.ok(enabledChatHtml.indexOf('yoagent-recent-agent-session">session 5') < enabledChatHtml.indexOf('yoagent-recent-agent-session">session 6'), 'YO!agent recent agents preserve backend recency order');
    api.applyActivitySummaryPayloadFromPushForTest({
      generated_at: '2026-05-31T12:05:00+00:00',
      global: {headline: 'Pushed summary should stay out of the printed startup block'},
      sessions: {},
      agents: [{session: '7', window_label: '0:claude', agent_kind: 'claude', label: "session '7' 0:claude", running: true}],
    });
    const pushUpdatedChatHtml = api.yoagentChatHtml();
    assert.ok(pushUpdatedChatHtml.includes('Your most recent work is about editor fixes'), 'activity-summary pushes do not repaint the one-shot startup summary');
    assert.equal(pushUpdatedChatHtml.includes('Pushed summary should stay out of the printed startup block'), false, 'activity-summary pushes are cache-only for the printed startup block');
    assert.equal(pushUpdatedChatHtml.includes('yoagent-recent-agent-session">session 7'), false, 'activity-summary pushes do not repaint printed Recent Agents');
    api.applyActivitySummaryPayloadFromPushForTest({
      generated_at: '2026-05-31T12:10:00+00:00',
      global: {headline: 'Manual refresh replaces the printed startup block'},
      sessions: {},
      agents: [{session: '8', window_label: '0:codex', agent_kind: 'codex', label: "session '8' 0:codex", running: true}],
    }, {refreshStartupSnapshot: true});
    const manuallyRefreshedChatHtml = api.yoagentChatHtml();
    assert.ok(manuallyRefreshedChatHtml.includes('Manual refresh replaces the printed startup block'), 'explicit activity-summary refresh replaces the startup summary snapshot');
    assert.ok(manuallyRefreshedChatHtml.includes('yoagent-recent-agent-session">session 8'), 'explicit activity-summary refresh replaces the Recent Agents snapshot');
    assert.equal(manuallyRefreshedChatHtml.includes('Your most recent work is about editor fixes'), false, 'explicit refresh removes the stale startup summary snapshot');
    api.setActivitySummaryPayloadForTest(baseActivitySummaryPayload);
    api.showYoagentStartupInfoForLatestActivityForTest();
    api.setYoagentMessagesForTest([{role: 'user', content: 'what changed?'}, {role: 'assistant', content: 'Checking the activity context.'}]);
    const chatWithHistoryHtml = api.yoagentChatHtml();
    assert.ok(chatWithHistoryHtml.includes('Checking the activity context.'), 'YO!agent chat keeps persisted messages');
    assert.ok(chatWithHistoryHtml.includes('yoagent-message assistant yoagent-recent-agents-message'), 'YO!agent chat keeps Recent Agents visible after a question');
    assert.ok(chatWithHistoryHtml.includes('Your most recent work is about editor fixes'), 'YO!agent chat keeps the current-work summary visible after a question');
    api.setActivitySummaryPayloadForTest({yoagent_summaries: {mode: 'first_launch', running: true, updated_ts: 1760000000, updated_at: '2025-10-09T08:53:20+00:00'}, global: {headline: 'Cached rolling context'}, sessions: {}, session_order: []});
    assert.equal(api.yoagentChatHtml().includes('Background transcript summaries on'), false, 'YO!agent chat no longer renders a continuous background-summary status notice');
    api.setActivitySummaryPayloadForTest(baseActivitySummaryPayload);
    assert.equal(enabledChatHtml.includes('yoagent-chat empty'), false, 'YO!agent intro is a regular message, not a special empty layout');
    assert.equal(enabledChatHtml.includes('yoagent-chat-toolbar'), false, 'YO!agent chat does not put Clear in a detached toolbar');
    assert.ok(enabledChatHtml.includes('yoagent-chat-controls'), 'YO!agent composer has a control row');
    assert.ok(enabledChatHtml.includes('data-yoagent-backend'), 'YO!agent composer shows the backend selector mapped to yoagent.backend');
    assert.ok(enabledChatHtml.includes('data-yoagent-model'), 'YO!agent composer shows the model selector');
    assert.ok(enabledChatHtml.includes('data-yoagent-effort'), 'YO!agent composer shows the effort selector');
    assert.ok(enabledChatHtml.indexOf('data-yoagent-backend') < enabledChatHtml.indexOf('data-yoagent-model'), 'YO!agent composer renders backend before model');
    assert.ok(enabledChatHtml.indexOf('data-yoagent-model') < enabledChatHtml.indexOf('data-yoagent-effort'), 'YO!agent composer renders model before effort');
    assert.ok(/data-yoagent-backend[\s\S]*?<option value="claude" selected/.test(enabledChatHtml), 'YO!agent composer selects the saved backend');
    const modelCatalogSource = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/'claude-fable-5': 'pref\.yoagent\.claude_model\.fable'/.test(modelCatalogSource), 'YO!agent frontend fallback includes the current generally available Claude Fable model');
    assert.ok(/'gpt-5\.3-codex-spark': 'pref\.yoagent\.codex_model\.gpt53spark'/.test(modelCatalogSource), 'YO!agent frontend fallback includes Codex Spark when the backend catalog is not loaded');
    assert.ok(/data-yoagent-model[\s\S]*data-yoagent-setting-path="yoagent\.claude_model"[\s\S]*?<option value="claude-opus-4-8"/.test(enabledChatHtml), 'YO!agent composer model options follow the selected backend');
    assert.ok(/data-yoagent-effort[\s\S]*data-yoagent-setting-path="yoagent\.claude_effort"[\s\S]*?<option value="low"/.test(enabledChatHtml), 'YO!agent composer effort options follow the selected backend');
    assert.equal(/data-yoagent-backend[\s\S]*?<option value="auto"/.test(enabledChatHtml), false, 'YO!agent composer backend selector does not offer Auto');
    assert.equal(/data-yoagent-backend[\s\S]*?<option value="deterministic"/.test(enabledChatHtml), false, 'YO!agent composer backend selector does not offer No agent as a selectable backend');
    assert.ok(enabledChatHtml.includes('yoagent-chat-send-icon'), 'YO!agent send button is a circular arrow icon');
    assert.ok(enabledChatHtml.includes('>Clear</button>'), 'YO!agent composer uses a compact Clear label');
    assert.equal(enabledChatHtml.includes('>Clear conversation</button>'), false, 'YO!agent composer does not use the long Clear conversation label');
    assert.ok(enabledChatHtml.indexOf('yoagent-chat-clear') < enabledChatHtml.indexOf('yoagent-chat-send'), 'YO!agent send arrow is the last (far-right) control, after Clear');
    api.setYoagentMessagesForTest([
      {role: 'user', content: 'first question', createdAt: '2026-06-13T17:38:00Z'},
      {role: 'assistant', content: 'first answer', createdAt: '2026-06-13T17:38:01Z'},
      {role: 'user', content: 'second question', createdAt: '2026-06-13T17:39:00Z'},
    ]);
    assert.ok(/:\d{2}\s*[AP]M\s*PDT/.test(api.yoagentChatHtml()), 'YO!agent message timestamps include seconds');
    api.setYoagentDraftForTest('new draft');
    const historyInput = {value: 'new draft', disabled: false, setSelectionRange(start, end) { this.selection = [start, end]; }};
    assert.deepStrictEqual(api.yoagentUserMessageHistoryForTest(), ['first question', 'second question'], 'YO!agent composer history contains only prior user messages');
    assert.equal(api.yoagentNavigateChatHistoryForTest(historyInput, 'up'), true, 'Up enters YO!agent composer history');
    assert.equal(historyInput.value, 'second question', 'first Up shows the most recent user message');
    assert.deepStrictEqual(historyInput.selection, ['second question'.length, 'second question'.length], 'history navigation places the cursor at the end for editing');
    assert.equal(api.yoagentNavigateChatHistoryForTest(historyInput, 'up'), true, 'repeated Up walks older');
    assert.equal(historyInput.value, 'first question', 'second Up shows the older user message');
    assert.equal(api.yoagentNavigateChatHistoryForTest(historyInput, 'up'), true, 'Up at the oldest message is handled');
    assert.equal(historyInput.value, 'first question', 'Up clamps at the oldest message');
    assert.equal(api.yoagentNavigateChatHistoryForTest(historyInput, 'down'), true, 'Down walks newer');
    assert.equal(historyInput.value, 'second question', 'first Down returns to the newer history message');
    assert.equal(api.yoagentNavigateChatHistoryForTest(historyInput, 'down'), true, 'Down from newest history returns to the latest draft slot');
    assert.equal(historyInput.value, 'new draft', 'latest slot restores the unsent draft so the placeholder is visible when blank');
    assert.equal(api.yoagentNavigateChatHistoryForTest(historyInput, 'down'), false, 'Down at the latest draft slot leaves the composer alone');
    api.applyYoagentConversationPayloadForTest({
      transcript_path: '/home/test/.local/state/yolomux/yoagent/conversation.jsonl',
      transcript_display_path: '~/.local/state/yolomux/yoagent/conversation.jsonl',
      messages: [{role: 'user', content: 'persisted question', createdAt: '2026-06-13T17:39:00Z'}],
    });
    const transcriptHtml = api.yoagentChatHtml();
    assert.ok(transcriptHtml.includes('yoagent-transcript-path'), 'YO!agent chat shows the persisted transcript location at the top');
    assert.ok(transcriptHtml.includes('~/.local/state/yolomux/yoagent/conversation.jsonl'), 'YO!agent transcript row uses the compact display path');
    assert.ok(transcriptHtml.includes('data-copy-path="/home/test/.local/state/yolomux/yoagent/conversation.jsonl"'), 'YO!agent transcript path can be copied');
    assert.equal(transcriptHtml.includes('yoagent-message assistant yoagent-recent-agents-message'), true, 'persisted YO!agent messages keep the one-shot Recent agents block visible');
    api.applyYoagentConversationPayloadForTest({
      messages: [{role: 'user', content: 'ask 6 and 7 for status', createdAt: '2026-06-13T17:39:00Z'}],
      pending_waits: [
        {id: 'wait-6', session: '6', started_ts: Date.now() / 1000 - 5, transcript: '/tmp/6.jsonl'},
        {id: 'wait-7', session: '7', started_ts: Date.now() / 1000 - 10, transcript: '/tmp/7.jsonl'},
      ],
    });
    const pendingWaitsHtml = api.yoagentChatHtml();
    assert.ok(pendingWaitsHtml.includes('yoagent-waiting-queue'), 'pending result waits render as a visible queue');
    assert.equal((pendingWaitsHtml.match(/class="yoagent-waiting-item"/g) || []).length, 2, 'multiple pending waits render as separate rows');
    assert.ok(pendingWaitsHtml.includes('data-yoagent-wait-clear="wait-6"'), 'pending waits expose a clear control');
    assert.ok(pendingWaitsHtml.includes('data-yoagent-wait-clear="wait-7"'), 'each pending wait can be cleared independently');
    assert.ok(/data-yoagent-chat-input[^>]*placeholder="Ask anything…"(?![^>]* disabled)/.test(pendingWaitsHtml), 'pending waits do not disable the YO!agent composer input');
    assert.ok(/class="yoagent-chat-send"(?![^>]* disabled)/.test(pendingWaitsHtml), 'pending waits do not disable the YO!agent send button');
    api.applyYoagentConversationPayloadForTest({
      messages: [
        {role: 'user', content: 'ask 6 for status', createdAt: '2026-06-13T17:39:00Z'},
        {role: 'assistant', kind: 'agent_result', session: '6', content: 'I sent the request to tmux session `6`, but I did not see a result before the wait timed out.', createdAt: '2026-06-13T17:40:00Z'},
      ],
      pending_waits: [],
    });
    const clearedWaitsHtml = api.yoagentChatHtml();
    assert.equal(clearedWaitsHtml.includes('yoagent-waiting-queue'), false, 'cleared server waits remove the pending queue');
    assert.ok(clearedWaitsHtml.includes('did not see a result before the wait timed out'), 'cleared waits leave the visible timeout/result message');
    assert.ok(/data-yoagent-chat-input[^>]*placeholder="Ask anything…"(?![^>]* disabled)/.test(clearedWaitsHtml), 'cleared waits keep the YO!agent composer input enabled');
    api.applyYoagentJobsPayloadForTest({
      jobs: [
        {id: 'job-confirm', type: 'wait_then_send', status: 'pending_confirmation', target: {session: '6'}, public_text: 'send date', last_observed_state: {blockers: ['target is busy']}},
        {id: 'job-queued', type: 'result_watch', status: 'queued', target: {roster: ['6', '7']}, action: {text_preview: 'wait for replies'}},
        {id: 'job-fired', type: 'wait_then_send', status: 'fired', session: '8', action: {text: 'echo done'}},
        {id: 'job-failed', type: 'wait_then_send', status: 'failed', target: {session: '9'}, error: 'timed out waiting'},
        {id: 'job-cancelled', type: 'wait_then_send', status: 'cancelled', target: {session: '10'}},
      ],
    });
    const jobsHtml = api.yoagentChatHtml();
    assert.ok(jobsHtml.includes('yoagent-jobs-list'), 'YO!agent jobs render as a visible queue in the chat history');
    assert.equal((jobsHtml.match(/class="yoagent-job-item/g) || []).length, 5, 'queued, pending, fired, failed, and cancelled jobs render as separate rows');
    assert.ok(jobsHtml.includes('data-yoagent-job-confirm="job-confirm"'), 'pending-confirmation jobs expose a confirm control');
    assert.ok(jobsHtml.includes('data-yoagent-job-cancel="job-confirm"'), 'pending-confirmation jobs expose a cancel control');
    assert.ok(jobsHtml.includes('data-yoagent-job-cancel="job-queued"'), 'queued jobs expose a cancel control');
    assert.equal(jobsHtml.includes('data-yoagent-job-confirm="job-fired"'), false, 'fired jobs do not expose stale confirm controls');
    assert.ok(jobsHtml.includes('target 6') && jobsHtml.includes('blocked by target is busy'), 'job rows show target sessions and blockers');
    assert.ok(jobsHtml.includes('send date') && jobsHtml.includes('wait for replies'), 'job rows show prompt/action previews');
    assert.ok(/data-yoagent-chat-input[^>]*placeholder="Ask anything…"(?![^>]* disabled)/.test(jobsHtml), 'visible jobs do not disable the YO!agent composer input');
    api.setYoagentMessagesForTest([
      {role: 'user', content: 'wait for session 6, then ask for date', createdAt: '2026-06-13T17:40:00Z'},
      {
        role: 'assistant',
        content: 'I resolved tmux session `6` and prepared a confirmed send action.',
        createdAt: '2026-06-13T17:40:01Z',
        details: '- backend: `claude`\n- response time: `1.234s` (`1234.0ms`)',
        responseMs: 5300,
        actions: [{
          id: 'ya_test',
          status: 'ready',
          session: '6',
          text: 'date',
          target: {session: '6', agent_kind: 'claude', transport: 'pane-paste', pane_target: '%6', cwd: '/repo/app'},
        }],
      },
      {
        role: 'assistant',
        kind: 'agent_result',
        session: '6',
        content: 'Result from tmux session `6`:\n\nThe date is June 13, 2026.',
        createdAt: '2026-06-13T17:41:00Z',
      },
    ]);
    const actionHtml = api.yoagentChatHtml();
    assert.ok(actionHtml.includes('yoagent-message user'), 'YO!agent user turns keep a role-specific bubble');
    assert.ok(actionHtml.includes('yoagent-message assistant'), 'YO!agent assistant turns keep a role-specific bubble');
    assert.ok(actionHtml.includes('5.3 seconds to respond'), 'YO!agent assistant headers show response latency from the persisted message field');
    assert.equal((actionHtml.match(/seconds to respond/g) || []).length, 1, 'YO!agent user turns do not show response latency');
    assert.ok(actionHtml.includes('yoagent-message assistant yoagent-agent-result'), 'YO!agent target-agent result turns get a distinct result bubble class');
    assert.ok(actionHtml.includes('yoagent-agent-result-heading') && actionHtml.includes('yoagent-agent-result-output'), 'YO!agent target-agent result splits the heading from the quoted output block');
    assert.ok(actionHtml.includes('class="yoagent-message-details"') && actionHtml.includes('response time:'), 'YO!agent assistant turns can expose expandable safe diagnostics');
    assert.ok(actionHtml.includes('data-yoagent-action-card="ya_test"'), 'YO!agent assistant turns render server-resolved action cards');
    assert.ok(actionHtml.includes('data-yoagent-action-send="ya_test"'), 'ready YO!agent action cards expose a confirmed send control');
    assert.ok(actionHtml.includes('Action preview') && actionHtml.includes('Send'), 'ready YO!agent action cards use localized action labels');
    assert.ok(actionHtml.includes('visible tmux pane'), 'ready YO!agent action cards label sends as visible-pane delivery');
    const openDetail = {dataset: {yoagentMessageDetailsKey: 'assistant|1'}, open: true};
    const closedDetail = {dataset: {yoagentMessageDetailsKey: 'assistant|2'}, open: false};
    const stateNode = {
      querySelectorAll(selector) {
        if (selector === '.yoagent-message-details[open][data-yoagent-message-details-key]') return [openDetail];
        if (selector === '.yoagent-message-details[data-yoagent-message-details-key]') return [closedDetail, openDetail];
        return [];
      },
    };
    const openKeys = api.yoagentOpenMessageDetailsStateForTest(stateNode);
    api.restoreYoagentOpenMessageDetailsStateForTest(stateNode, openKeys);
    assert.deepStrictEqual([...openKeys], ['assistant|1'], 'YO!agent captures the opened Details message key before repaint');
    assert.equal(openDetail.open, true, 'YO!agent restores the matching Details block after repaint');
    assert.equal(closedDetail.open, false, 'YO!agent does not expand unrelated Details blocks after repaint');
    api.setYoagentBusyForTest(true);
    assert.ok(api.yoagentChatHtml().includes('yoagent-chat-spinner'), 'YO!agent busy state includes an animated spinner');
    // The "thinking" label keeps its word but the trailing dots are CSS-animated, so the text updates
    // without rebuilding the busy-state DOM.
    assert.ok(api.yoagentChatHtml().includes('thinking'), 'YO!agent busy state keeps the concise thinking label');
    assert.ok(api.yoagentChatHtml().includes('yoagent-thinking-dots'), 'YO!agent thinking dots are CSS animated, not hardcoded static text');
    assert.ok(api.yoagentChatHtml().includes('session-yolo-marker active working'), 'YO!agent busy spinner reuses the YO tab working marker');
    api.setYoagentMessagesForTest([
      {
        role: 'assistant',
        content: 'Done',
        createdAt: '2026-06-13T17:42:00Z',
        details: 'usage: {"cache_creation":{"input_tokens":123}}',
        auxiliaryPreview: 'usage: {"cache_creation":{"input_tokens":123}}',
      },
    ]);
    const usageOnlyHtml = api.yoagentChatHtml();
    const usageOnlySummary = usageOnlyHtml.match(/<summary>[\s\S]*?<\/summary>/)?.[0] || '';
    assert.ok(/<summary><span>details…<\/span><\/summary>/.test(usageOnlyHtml), 'YO!agent diagnostic-only details collapse to just details…');
    assert.ok(/<pre class="yoagent-safe-details">usage:/.test(usageOnlyHtml), 'YO!agent usage diagnostics remain visible inside the expanded details body');
    assert.equal(usageOnlySummary.includes('usage:'), false, 'YO!agent usage diagnostics do not leak into the collapsed summary preview');
    api.setYoagentMessagesForTest([
      {
        role: 'assistant',
        content: 'Done',
        createdAt: '2026-06-13T17:42:01Z',
        details: '- response time: `1.000s` (`1000.0ms`)',
        auxiliaryLines: ['thinking: reading activity context'],
        auxiliaryPreview: 'thinking: reading activity context',
      },
    ]);
    const thinkingPreviewHtml = api.yoagentChatHtml();
    assert.ok(thinkingPreviewHtml.includes('<summary><span>thinking (4 words)…</span></summary>'), 'completed YO!agent thinking details collapse to a count-only summary');
    assert.equal(thinkingPreviewHtml.includes('yoagent-details-preview'), false, 'completed YO!agent thinking details do not keep preview words in the collapsed summary');
    assert.ok(thinkingPreviewHtml.includes('<pre class="yoagent-auxiliary-stream">thinking: reading activity context</pre>'), 'expanded completed YO!agent thinking details keep the real thinking text');
    api.setYoagentBusyForTest(false);
    api.setYoagentDraftForTest('half typed question');
    assert.ok(api.yoagentChatHtml().includes('value="half typed question"'), 'YO!agent chat draft survives summary refresh re-renders');
    api.setYoagentErrorForTest("Couldn't reach the YOLOmux server. Your question is still in the box; retry after the server is back.");
    assert.ok(api.yoagentChatHtml().includes('data-yoagent-retry'), 'YO!agent network failures show a retry action without losing the draft');
    api.setYoagentErrorForTest('');
    api.setYoagentNoticeForTest({backend: 'claude', reason: 'Claude CLI is not logged in. Run `claude login`.'});
    assert.ok(api.yoagentChatHtml().includes('yoagent-chat-notice'), 'YO!agent chat surfaces backend fallback notices');
    assert.ok(api.yoagentChatHtml().includes('claude'), 'YO!agent fallback notice includes the backend');
    assert.ok(api.yoagentChatHtml().includes('claude login'), 'YO!agent fallback notice includes the login action');
    assert.ok(api.globalActivitySummaryHtml().includes('3 files changed (+9/-2)'), 'global activity summary renders file totals');
    assert.ok(api.globalActivitySummaryHtml().includes('data-yoagent-global-markdown'), 'global activity summary preserves markdown as escaped fallback until the render pass');
    assert.ok(api.globalActivitySummaryHtml().includes('Your most recent work is about editor fixes'), 'global activity summary renders a human sentence');
    assert.equal(api.globalActivitySummaryHtml().includes('Session alpha'), false, 'global activity summary omits per-session detail lines');
    assert.equal(api.sessionActivitySummary('alpha').local, "Codex session alpha is active in yolomux.dev. It has been working on editor fixes. It currently has 2 files changed (+8/-1).");
    api.setTranscriptInfoForTest('alpha', {
      project: {
        git: {
          root: '/repo/alpha',
          branch: 'zeta',
          other_branches: {
            branches: [
              {name: 'zeta', current: true, updated: 'yesterday', updated_ts: 100, subject: 'second item', linear_ids: ['GH-2']},
            ],
          },
        },
      },
    });
    api.setTranscriptInfoForTest('beta', {
      project: {
        git: {
          root: '/repo/beta',
          branch: 'alpha',
          other_branches: {
            branches: [
              {name: 'alpha', current: true, updated: 'today', updated_ts: 200, subject: 'first item', linear_ids: ['GH-1']},
            ],
          },
        },
      },
    });

    assert.deepStrictEqual(canonical(api.infoBranchRows().map(row => row.session)), ['alpha / no AI', 'beta / no AI']);
    api.setInfoGroupingForTest(['pr', 'tab', 'path', 'branch']);
    api.setInfoSortForTest({key: 'name', dir: 'desc'});
    api.setInfoSearchForTest('alpha pr', {publish: false});
    const shareInfoSnapshot = api.shareUiStateSnapshotForTest().info;
    assert.equal('branchSort' in shareInfoSnapshot, false, 'YO!share no longer snapshots deleted YO!info table branch-sort state');
    assert.deepStrictEqual(canonical(shareInfoSnapshot.grouping), ['pr', 'tab', 'path', 'branch'], 'YO!share snapshots the host YO!info grouping order');
    assert.deepStrictEqual(canonical(shareInfoSnapshot.sort), {dir: 'desc', key: 'name'}, 'YO!share snapshots the host YO!info sort mode');
    assert.equal(shareInfoSnapshot.search, 'alpha pr', 'YO!share snapshots the host YO!info search query');
    assert.deepStrictEqual(canonical(shareInfoSnapshot.branchRows.map(row => row.session)), ['alpha / no AI', 'beta / no AI'], 'YO!share snapshots host-owned YO!info rows');
    assert.equal('columnWidths' in shareInfoSnapshot, false, 'YO!share no longer snapshots deleted YO!info table column widths');
    const shareApi = loadYolomux('?shareReplay=0', ['1'], 'https:', 'Linux x86_64', 'readonly', {
      share: {view: true, id: 'share-info', mode: 'ro', session: '1', sessions: ['1']},
    });
    const shareInfoScroller = shareApi.testElementForId('info-content');
    shareInfoScroller.scrollTop = 0;
    shareInfoScroller.scrollLeft = 0;
    shareApi.setTranscriptInfoForTest('1', {
      project: {
        git: {
          root: '/repo/client-only',
          branch: 'client-local',
          other_branches: {
            branches: [
              {name: 'client-local', current: true, updated: 'now', updated_ts: 999, subject: 'must not render'},
            ],
          },
        },
      },
    });
    assert.deepStrictEqual(canonical(shareApi.infoBranchRows().map(row => row.session)), ['1 / no AI'], 'share client starts with local YO!info rows before a host snapshot arrives');
    shareApi.applyShareUiStateForTest({info: {branchSort: {key: 'session', dir: 'desc'}, grouping: shareInfoSnapshot.grouping, sort: shareInfoSnapshot.sort, search: shareInfoSnapshot.search, columnWidths: {branch: 610, desc: 820}, branchRows: shareInfoSnapshot.branchRows}});
    assert.equal('branchSort' in shareApi.shareUiStateSnapshotForTest().info, false, 'share viewers ignore legacy YO!info table sort snapshots');
    assert.deepStrictEqual(canonical(shareApi.currentInfoGroupingForTest()), ['pr', 'tab', 'path', 'branch'], 'share viewers apply host YO!info grouping state');
    assert.deepStrictEqual(canonical(shareApi.currentInfoSortForTest()), {dir: 'desc', key: 'name'}, 'share viewers apply host YO!info sort state');
    assert.equal(shareApi.currentInfoSearchForTest(), 'alpha pr', 'share viewers apply host YO!info search state');
    assert.deepStrictEqual(canonical(shareApi.infoBranchRows().map(row => row.session)), ['alpha / no AI', 'beta / no AI'], 'share viewers render host-owned YO!info rows instead of local transcript metadata');
    assert.equal('columnWidths' in shareApi.shareUiStateSnapshotForTest().info, false, 'share viewers ignore legacy YO!info table column widths');
    shareApi.applyShareScrollStateForTest({target: 'info', kind: 'info', top: 88, left: 144});
    assert.equal(shareInfoScroller.scrollTop, 88, 'share viewers apply YO!info vertical host scroll');
    assert.equal(shareInfoScroller.scrollLeft, 144, 'share viewers apply YO!info horizontal host scroll');
    shareInfoScroller.scrollTop = 0;
    shareInfoScroller.scrollLeft = 0;
    shareApi.restoreShareReadonlyScrollTargetForTest(shareInfoScroller);
    assert.equal(shareInfoScroller.scrollTop, 88, 'readonly YO!info local vertical scroll restores to the host position');
    assert.equal(shareInfoScroller.scrollLeft, 144, 'readonly YO!info local horizontal scroll restores to the host position');
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    const infoPanelSource = fs.readFileSync('static_src/js/yolomux/80_info_panel.js', 'utf8');
    const terminalBootSource = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
    assert.ok(/function bindInfoPanel\(panel\)[\s\S]*delegate\(panel, 'click', '\[data-info-refresh\]'[\s\S]*delegate\(panel, 'click', '\[data-info-preset\]'[\s\S]*delegate\(panel, 'click', '\[data-info-open-path\]'/.test(infoPanelSource), 'YO!info tree click actions bind once on the persistent panel root');
    assert.ok(/panel\.addEventListener\('toggle'[\s\S]*details\[data-info-group-key\][\s\S]*setInfoTreeGroupCollapsed/.test(infoPanelSource), 'YO!info tree group collapse state is captured on the persistent panel root');
    assert.ok(/const infoCollapsedGroupKeys = new Set\(\)[\s\S]*function infoTreeGroupCollapseKey[\s\S]*data-info-group-key="\$\{esc\(groupKey\)\}"\$\{openAttr\}/.test(terminalBootSource), 'YO!info group renderer uses stable group keys instead of forcing every details node open');
    assert.equal(/function bindInfoColumnResizers/.test(terminalBootSource), false, 'old YO!info table column resizer owner is removed');
    assert.equal(/dataset\.bound/.test(terminalBootSource), false, 'YO!info column resizers do not use the dead per-handle dataset.bound guard');
    assert.equal(/querySelectorAll\('\[data-info-sort\]'\)[\s\S]{0,180}addEventListener\('click'/.test(terminalBootSource), false, 'renderInfoPanel does not reattach sort click listeners after every repaint');
    assert.equal(/querySelectorAll\('\[data-info-session-drawer\]'\)[\s\S]{0,180}addEventListener\('click'/.test(terminalBootSource), false, 'renderInfoPanel does not reattach drawer click listeners after every repaint');
    assert.equal(/querySelectorAll\('\[data-watched-remove\]'\)[\s\S]{0,180}addEventListener\('click'/.test(terminalBootSource), false, 'renderWatchedPrs does not reattach remove click listeners after every repaint');
    assert.equal(/document\.querySelectorAll\('\[data-info-refresh\]'\)/.test(terminalBootSource), false, 'metadata loading refreshes scope the YO!info refresh button instead of scanning the whole document');
    assert.equal(/function setInfoColumnWidth/.test(source), false, 'deleted YO!info table column width code is not retained');
    assert.ok(/function shareInfoStateSnapshot\(options = \{\}\)[\s\S]*options\.includeRows !== false[\s\S]*snapshot\.branchRows = infoBranchRows\(\)\.map\(shareInfoRowSnapshot\)/.test(source), 'YO!share info snapshots include host YO!info rows when full state is requested');
    assert.ok(/function shareInfoStateSnapshot\(options = \{\}\)[\s\S]*grouping:\s*currentInfoGrouping\(\)[\s\S]*sort:\s*currentInfoSort\(\)[\s\S]*search:\s*currentInfoSearch\(\)/.test(source), 'YO!share info snapshots include host YO!info grouping, sort, and search state');
    assert.ok(/function applyShareInfoState\(info = \{\}\)[\s\S]*shareInfoBranchRowsOverride = cleanShareInfoRows\(info\.branchRows\)[\s\S]*renderInfoPanel\(\)/.test(source), 'share clients apply host YO!info rows without persisting or echo-publishing');
  });

  await testAsync('YO!agent chat queue waits for pending target-agent waits before sending', async () => {
    const api = loadYolomux('', ['alpha'], 'http:', 'Linux x86_64', 'admin', {
      bootstrapOverrides: {
        availableAgents: ['claude'],
        agentAuth: {claude: {installed: true, logged_in: true}},
      },
    });
    api.setClientSettingsPatchForTest({yoagent: {backend: 'claude'}});
    const chatPosts = [];
    api.setFetchForTest((url, options = {}) => {
      const path = String(url);
      if (path === '/api/yoagent/chat') {
        const body = JSON.parse(options.body || '{}');
        chatPosts.push(body.message);
        return Promise.resolve(jsonResponse({
          backend: 'claude',
          backend_used: 'claude',
          answer: `${body.message} answer`,
          conversation: {
            messages: [
              {role: 'user', content: body.message, createdAt: '2026-06-13T17:39:00Z'},
              {role: 'assistant', content: `${body.message} answer`, createdAt: '2026-06-13T17:39:01Z'},
            ],
            pending_waits: [],
          },
        }));
      }
      return Promise.resolve(jsonResponse({messages: [], pending_waits: []}));
    });

    api.applyYoagentConversationPayloadForTest({
      messages: [{role: 'user', content: 'ask alpha for status', createdAt: '2026-06-13T17:38:00Z'}],
      pending_waits: [{id: 'wait-alpha', session: 'alpha', started_ts: Date.now() / 1000, transcript: '/tmp/alpha.jsonl'}],
    });
    await api.sendYoagentChatMessageForTest('second ask');
    assert.deepStrictEqual(chatPosts, [], 'pending target-agent waits keep later asks in the local queue');
    assert.deepStrictEqual(canonical(api.yoagentChatQueueForTest().map(item => item.text)), ['second ask'], 'later ask is visible as queued text');

    api.applyYoagentConversationPayloadForTest({
      messages: [{role: 'assistant', kind: 'agent_result', session: 'alpha', content: 'alpha result', createdAt: '2026-06-13T17:39:00Z'}],
      pending_waits: [],
    });
    for (let i = 0; i < 4; i += 1) await flushAsyncWork();
    assert.deepStrictEqual(chatPosts, ['second ask'], 'queued ask is sent only after the pending wait clears');
    assert.deepStrictEqual(canonical(api.yoagentChatQueueForTest()), [], 'sent ask is removed from the queue');
  });

  test('t@6976', () => {
    const api = loadYolomux();
    assert.equal(api.dedentSelectionText('  hello\n  world'), 'hello\nworld');
    assert.equal(api.dedentSelectionText('  hello\n    world'), 'hello\n  world');
    assert.equal(api.dedentSelectionText('\n  hello\n  world\n'), '\nhello\nworld\n');
    assert.equal(api.dedentSelectionText('hello\n  world'), 'hello\nworld');
    assert.equal(api.dedentSelectionText('● 1\n  2\n  3'), '1\n2\n3');
    assert.equal(api.dedentSelectionText('• answer'), 'answer');
    assert.equal(api.dedentSelectionText('• answer:\n\n  \"  hello\\n  world\"'), 'answer:\n\n\"  hello\\n  world\"');
  });

  test('t@6987', () => {
    const api = loadYolomux();
    const lines = [
      terminalLine('https://ex'),
      terminalLine('ample.com/', true),
      terminalLine('abcdef', true),
    ];
    const term = {
      buffer: {
        active: {
          getLine(index) {
            return lines[index] || null;
          },
        },
      },
    };

    const middleLinks = api.terminalWrappedLineLinks(term, 2);
    assert.equal(middleLinks.length, 1);
    assert.equal(middleLinks[0].text, 'https://example.com/abcdef');
    assert.equal(middleLinks[0].type, 'url');
    assert.deepStrictEqual(canonical(middleLinks[0].range), {
      start: {x: 1, y: 1},
      end: {x: 6, y: 3},
    });

    const lastLinks = api.terminalWrappedLineLinks(term, 3);
    assert.equal(lastLinks.length, 1);
    assert.equal(lastLinks[0].text, 'https://example.com/abcdef');
  });

  // an agent HARD-wraps a long URL with a HANGING INDENT — the continuation is its own logical
  // line (isWrapped === false), indented under the URL column. Stitch it onto the link so the whole URL
  // is one clickable link, underlined across both rows at their real columns.
  test('t@7020', () => {
    const api = loadYolomux();
    const lines = [
      terminalLine('https://github.com/ai-dynamo/frontend-crates/actions/runs/26'),
      terminalLine('    919558600/job', false),  // hanging indent (4 spaces), NOT a soft-wrap
      terminalLine('$ ', false),                 // a plain prompt row — must NOT be merged
    ];
    // the URL row fills the terminal to its right edge (cols == its length), proving it was
    // CLIPPED and hard-wrapped — that is what licenses stitching the indented continuation onto it.
    const term = {cols: lines[0].translateToString(true).length, buffer: {active: {getLine: index => lines[index] || null}}};

    const full = 'https://github.com/ai-dynamo/frontend-crates/actions/runs/26919558600/job';
    // Query from the FIRST row.
    const firstRow = api.terminalWrappedLineLinks(term, 1);
    assert.equal(firstRow.length, 1, 'the hard-wrapped URL is one link when hovering row 1');
    assert.equal(firstRow[0].text, full, 'the link text is the full stitched URL');
    assert.equal(firstRow[0].range.start.y, 1);
    assert.equal(firstRow[0].range.start.x, 1);
    assert.equal(firstRow[0].range.end.y, 2, 'the underline extends onto the continuation row');
    // continuation '919558600/job' is 13 chars after a 4-space indent → last char at column 4 + 13 = 17.
    assert.equal(firstRow[0].range.end.x, 17, 'the continuation underline lands at its REAL (indented) columns');

    // Query from the CONTINUATION row — same link (backward sweep finds the URL start).
    const contRow = api.terminalWrappedLineLinks(term, 2);
    assert.equal(contRow.length, 1, 'the link is also active when hovering the continuation row');
    assert.equal(contRow[0].text, full);
    assert.equal(contRow[0].range.start.y, 1);
    assert.equal(contRow[0].range.end.y, 2);

    // A plain prompt row below is NOT part of the link.
    const promptRow = api.terminalWrappedLineLinks(term, 3);
    assert.equal(promptRow.length, 0, 'the prompt row after the URL is not merged into the link');
  });

  // Some TUIs hard-wrap long URLs as separate, flush-left rows at width-1 to avoid xterm auto-wrap.
  // The shared reference detector still needs to stitch those rows into one URL for the context menu.
  test('t@7036', () => {
    const api = loadYolomux();
    const linkProviderSource = fs.readFileSync('static_src/js/yolomux/10_core_utils.js', 'utf8');
    const terminalCss = fs.readFileSync('static_src/css/yolomux/50_terminal_file_tree.css', 'utf8');
    const runtimeSource = fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8');
    const terminalBootSource = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
    const providerStart = linkProviderSource.indexOf('function installTerminalLinkProvider');
    const providerEnd = linkProviderSource.indexOf('function terminalCellDimensions', providerStart);
    const providerSource = linkProviderSource.slice(providerStart, providerEnd);
    assert.ok(/activate: \(\) => \{\}/.test(linkProviderSource), 'xterm link decorations have an explicit no-op left-click handler');
    assert.ok(/decorations: \{underline: true, pointerCursor: false\}/.test(linkProviderSource), 'terminal URL/file references are visibly underlined without showing a left-click pointer affordance');
    assert.ok(linkProviderSource.includes('function installTerminalFileReferenceUnderlines'), 'existing terminal file refs have a persistent underline overlay owner');
    assert.ok(/function terminalFileReferenceViewportSignature\(term\)/.test(linkProviderSource), 'terminal file underline scheduling keys cheap viewport state');
    assert.ok(/const scheduleCachedRender = \(\) => \{[\s\S]*if \(renderFrame\) return;[\s\S]*requestAnimationFrame/.test(linkProviderSource), 'terminal file underline cached repaint is coalesced through one frame');
    assert.ok(/const contentChanged = scheduleOptions\.contentChanged === true \|\| \['output', 'render'\]\.includes\(scheduleOptions\.reason\);[\s\S]*if \(viewportChanged \|\| contentChanged\) scheduleCachedRender\(\);[\s\S]*if \(!timer\)/.test(linkProviderSource), 'terminal output clears stale cached underlines immediately and cannot postpone the bounded resolver with continuous renders');
    assert.equal(/const schedule = \(scheduleOptions = \{\}\) => \{[\s\S]{0,360}renderCached\(\);[\s\S]{0,200}setTimeout/.test(linkProviderSource), false, 'terminal output does not synchronously repaint cached file underlines before the 90ms resolver');
    assert.equal(providerSource.includes('window.open'), false, 'xterm link provider must not open browser tabs from left-click activation');
    assert.ok(/function applyTerminalContainerTheme\(container[\s\S]*dataset\.terminalTheme = resolvedTerminalThemeMode\(terminalThemeMode, mode\)[\s\S]*style\.background = theme\.background/.test(runtimeSource), 'terminal containers carry the resolved terminal theme used by xterm link underline colors');
    assert.ok(/applyTerminalContainerTheme\(container, baseTheme\)/.test(terminalBootSource), 'new terminal containers get the same theme marker as live theme updates');
    assert.ok(/installTerminalFileReferenceUnderlines\(session, term, container\)/.test(terminalBootSource), 'new terminal containers install persistent existing-file underline overlays');
    assert.ok(/\.terminal\s*\{[\s\S]*--terminal-file-link-underline:\s*rgb\(125 211 252 \/ 0\.50\)[\s\S]*--terminal-file-link-underline-hover:\s*rgb\(125 211 252 \/ 0\.60\)/.test(terminalCss), 'dark terminal existing-file underlines stay visible on dark backgrounds');
    assert.ok(/\.terminal\[data-terminal-theme="light"\]\s*\{[\s\S]*--terminal-file-link-underline:\s*rgb\(3 105 161 \/ 0\.48\)[\s\S]*--terminal-file-link-underline-hover:\s*rgb\(3 105 161 \/ 0\.58\)/.test(terminalCss), 'light terminal existing-file underlines stay visible on white backgrounds');
    assert.ok(/\.terminal-file-link-underlines\s*\{[\s\S]*z-index:\s*var\(--z-terminal-overlay-low\)[\s\S]*pointer-events:\s*none/.test(terminalCss), 'persistent terminal file underlines render above xterm without stealing hover or selection');
    assert.ok(/\.terminal-file-link-underline\s*\{[\s\S]*border-bottom:\s*1px solid var\(--terminal-file-link-underline\)/.test(terminalCss), 'persistent terminal file underlines are a one-pixel cue');
    assert.ok(/\.terminal-file-link-underline--hover\s*\{[\s\S]*border-bottom-color:\s*var\(--terminal-file-link-underline-hover\)[\s\S]*border-bottom-width:\s*1px/.test(terminalCss), 'hovered resolved terminal file refs keep a subtle underline overlay');
    assert.ok(/\.terminal \.xterm-rows span\[style\*="text-decoration: underline"\],[\s\S]*span\[style\*="text-decoration-line: underline"\]\s*\{[\s\S]*text-decoration-color:\s*currentColor !important[\s\S]*text-decoration-thickness:\s*1px !important[\s\S]*text-underline-offset:\s*2px !important/.test(terminalCss), 'xterm hover-underlined URL/file spans use the hovered text color and a subtle underline');
    const first = 'https://claude.com/cai/oauth/authorize?client_id=abc123&scope=org%3Acreate_a';
    const continuations = [
      'pi_key+user%3Aprofile+user%3Ainference&redirect_uri=http%3A%2F%2FlocalhostABCDEF',
      '%3A54545%2Fcallback&code_challenge=GSappE0',
    ];
    const full = `${first}${continuations.join('')}`;
    for (const cols of [first.length, first.length + 1]) {
      const lines = [
        terminalLine(first, false),
        terminalLine(continuations[0], false),
        terminalLine(continuations[1], false),
        terminalLine('$ ', false),
      ];
      const term = {cols, buffer: {active: {getLine: index => lines[index] || null}}};
      for (const y of [1, 2, 3]) {
        const links = api.terminalWrappedLineLinks(term, y);
        assert.equal(links.length, 1, `zero-indent width ${cols} row ${y} resolves one stitched URL`);
        assert.equal(links[0].text, full, `zero-indent width ${cols} row ${y} returns the full URL; got ${links[0].text}`);
        assert.deepStrictEqual(canonical(links[0].range), {
          start: {x: 1, y: 1},
          end: {x: continuations[1].length, y: 3},
        }, `zero-indent width ${cols} row ${y} underlines the full 3-row URL`);
      }
      assert.equal(api.terminalWrappedLineLinks(term, 4).length, 0, `zero-indent width ${cols} stops before the prompt row`);
    }
  });

  test('t@7059', () => {
    const api = loadYolomux();
    api.setTranscriptInfoForTest('1', {selected_pane: {current_path: '/home/test/yolomux.dev3'}});
    const lines = [
      terminalLine('• Documented it in docs/specs/SHARE_TEST_INVENTORY.md:123'),
      terminalLine('Open https://example.com/guide here'),
    ];
    const term = {cols: 80, rows: 10, buffer: {active: {viewportY: 0, getLine: index => lines[index] || null}}};
    const refs = api.terminalWrappedLineReferences(term, 1);
    const fileRef = refs.find(ref => ref.type === 'file');
    assert.deepStrictEqual(canonical({
      text: fileRef?.text,
      path: fileRef?.path,
      line: fileRef?.line,
      range: fileRef?.range,
    }), {
      text: 'docs/specs/SHARE_TEST_INVENTORY.md:123',
      path: 'docs/specs/SHARE_TEST_INVENTORY.md',
      line: 123,
      range: {start: {x: 20, y: 1}, end: {x: 57, y: 1}},
    }, 'terminal output detects relative file:line references as context-menu references');
    assert.equal(api.terminalFileReferenceAbsolutePath('1', fileRef), '/home/test/yolomux.dev3/docs/specs/SHARE_TEST_INVENTORY.md', 'relative terminal file refs resolve against the active pane cwd');
    assert.equal(api.terminalWrappedLineLinks(term, 1).some(ref => ref.type === 'file'), true, 'file references are visually marked for right-click open/copy actions');
    assert.equal(api.terminalReferenceAtPosition(term, {x: 32, y: 1})?.text, 'docs/specs/SHARE_TEST_INVENTORY.md:123', 'right-click hit-testing finds the file ref under the cursor');
    const urlRef = api.terminalReferenceAtPosition(term, {x: 8, y: 2});
    assert.equal(urlRef.type, 'url', 'right-click hit-testing still finds URLs');
    assert.equal(urlRef.href, 'https://example.com/guide');
    assert.equal(typeof urlRef.activate, 'undefined', 'URL references have no left-click activation handler');

    const qwenLines = [
      terminalLine('protocols/openai/chat_completions/qwen3_coder_v2.rs'),
      terminalLine('- Streaming - preprocessor.rs:1972'),
    ];
    const qwenTerm = {cols: 100, rows: 10, buffer: {active: {viewportY: 0, getLine: index => qwenLines[index] || null}}};
    const qwenRef = api.terminalWrappedLineReferences(qwenTerm, 1).find(ref => ref.type === 'file');
    assert.equal(qwenRef?.path, 'protocols/openai/chat_completions/qwen3_coder_v2.rs', 'terminal output detects qwen-style repo-relative Rust paths');
    api.setTranscriptInfoForTest('1', {selected_pane: {current_path: '/home/test/dynamo4/lib/llm/src'}});
    assert.equal(api.terminalFileReferenceAbsolutePath('1', qwenRef), '/home/test/dynamo4/lib/llm/src/protocols/openai/chat_completions/qwen3_coder_v2.rs', 'qwen-style paths resolve against the live terminal cwd');
    const basenameRef = api.terminalWrappedLineReferences(qwenTerm, 2).find(ref => ref.type === 'file');
    assert.deepStrictEqual(canonical({path: basenameRef?.path, line: basenameRef?.line}), {path: 'preprocessor.rs', line: 1972}, 'terminal output detects basename.rs:line references in prose');

    const container = api.testElementForId('terminal-pane-1');
    container.rect = {left: 0, top: 0, width: 800, height: 200, right: 800, bottom: 200};
    term._core = {_renderService: {dimensions: {css: {cell: {width: 10, height: 20}}}}};
    assert.deepStrictEqual(canonical(api.terminalPositionFromClientPoint(term, container, 315, 10)), {x: 32, y: 1}, 'client point maps to the terminal cell used by context-menu hit-testing');
  });

  testAsync('t@7064', async () => {
    const api = loadYolomux();
    const line = 'Open static_src/js/yolomux/00_bootstrap_state.js:283 and missing.js:9';
    const lines = [terminalLine(line)];
    const term = {
      cols: 100,
      rows: 3,
      buffer: {active: {viewportY: 0, getLine: index => lines[index] || null}},
      _core: {_renderService: {dimensions: {css: {cell: {width: 10, height: 20}}}}},
    };
    const container = new TestElement('terminal-pane-1');
    container.className = 'terminal';
    container.rect = {left: 0, top: 0, width: 1000, height: 60, right: 1000, bottom: 60};
    const rows = new TestElement('terminal-rows');
    rows.className = 'xterm-rows';
    rows.rect = {left: 0, top: 0, width: 1000, height: 60, right: 1000, bottom: 60};
    container.appendChild(rows);

    let resolverCalls = 0;
    const controller = api.installTerminalFileReferenceUnderlines('1', term, container, {
      targetResolver: async (_session, ref) => {
        resolverCalls += 1;
        return ref.path === 'static_src/js/yolomux/00_bootstrap_state.js'
          ? {path: `/repo/${ref.path}`}
          : null;
      },
    });
    const count = await controller.refresh();
    const layer = container.querySelector(':scope > .terminal-file-link-underlines');
    assert.equal(count, 1, 'only existing terminal file references get a persistent underline');
    assert.equal(layer.children.length, 1, 'missing file references do not get underline segments');
    assert.equal(layer.children[0].dataset.path, '/repo/static_src/js/yolomux/00_bootstrap_state.js');
    assert.equal(layer.children[0].dataset.text, 'static_src/js/yolomux/00_bootstrap_state.js:283');
    assert.ok(layer.children[0].dataset.referenceKey.includes('static_src/js/yolomux/00_bootstrap_state.js'), 'overlay segment records a stable file-reference hover key');
    assert.equal(layer.children[0].style.left, '50px', 'underline starts at the path column');
    assert.equal(layer.children[0].style.top, '18px', 'underline sits near the row baseline');
    assert.equal(layer.children[0].style.width, '470px', 'underline width covers the path plus line number');
    container.listeners.get('mousemove')[0]({clientX: 55, clientY: 10});
    assert.equal(layer.children[0].classList.contains('terminal-file-link-underline--hover'), true, 'hovering a resolved file reference marks its subtle underline overlay');
    container.listeners.get('mousemove')[0]({clientX: 930, clientY: 10});
    assert.equal(layer.children[0].classList.contains('terminal-file-link-underline--hover'), false, 'moving away from the resolved file reference restores the subtle underline');
    container.listeners.get('mouseleave')[0]();
    assert.equal(layer.children[0].classList.contains('terminal-file-link-underline--hover'), false, 'leaving the terminal clears the file underline hover state');
    const callsAfterRefresh = resolverCalls;
    controller.schedule({reason: 'output'});
    assert.equal(resolverCalls, callsAfterRefresh, 'same-viewport terminal output does not synchronously re-resolve file references');
    assert.equal(layer.children.length, 1, 'terminal writes immediately repaint a still-visible cached file reference without re-resolving it');
    term.buffer.active.viewportY = 10;
    controller.schedule({reason: 'scroll', viewportChanged: true});
    assert.equal(layer.children.length, 0, 'underlines clear when the resolved file reference scrolls out of the visible viewport');
    term.buffer.active.viewportY = 0;
    controller.schedule({reason: 'scroll', viewportChanged: true});
    assert.equal(layer.children.length, 1, 'scrolling a cached resolved file reference back into view redraws from cache on the coalesced frame');
    lines[0] = terminalLine('No file references here');
    controller.schedule({reason: 'output'});
    assert.equal(layer.children.length, 0, 'same-viewport terminal output clears stale underline segments on the coalesced cached repaint');
    assert.equal(await controller.refresh(), 0, 'the trailing resolver confirms the removed reference stays absent');
    controller.dispose();
    assert.equal(container.listeners.get('mousemove').length, 0, 'terminal file underline hover listener is removed on dispose');
    assert.equal(container.listeners.get('mouseleave').length, 0, 'terminal file underline leave listener is removed on dispose');
    assert.equal(container.querySelector(':scope > .terminal-file-link-underlines'), null, 'underline overlay is removed on dispose');
  });

  test('terminal output scan schedulers are coalesced and gated', () => {
    const layoutSource = fs.readFileSync('static_src/js/yolomux/70_layout_actions.js', 'utf8');
    const terminalBootSource = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
    assert.ok(/function scheduleTerminalAttentionHighlight\(session\)[\s\S]*if \(item\.attentionHighlightFrame\) return;[\s\S]*item\.attentionHighlightFrame = requestAnimationFrame/.test(layoutSource), 'terminal attention highlight scans are coalesced to one frame per terminal');
    assert.ok(/if \(item\.attentionHighlightFrame\) cancelAnimationFrame\(item\.attentionHighlightFrame\)/.test(layoutSource), 'pending terminal attention highlight frames are cancelled with terminal teardown');
    assert.ok(/const terminalBlankScreenRefreshRiskReasons = Object\.freeze\(new Set\(\[[\s\S]*'socket-open'[\s\S]*'fit'[\s\S]*'first-output'[\s\S]*'terminal-tab'/.test(layoutSource), 'blank-screen refresh scheduling is limited to known risk windows');
    assert.ok(/function scheduleTerminalBlankScreenRefresh\(session, options = \{\}\)[\s\S]*!terminalBlankScreenRefreshAllowed\(reason\)[\s\S]*item\.socket\?\.readyState !== WebSocket\.OPEN[\s\S]*item\.blankScreenRefreshTimer && options\.reset !== true/.test(layoutSource), 'blank-screen refresh probes are gated before scanning terminal rows');
    assert.ok(/socket\.onopen = \(\) => \{[\s\S]*item\.terminalOutputSeen = false;[\s\S]*scheduleTerminalBlankScreenRefresh\(session, \{reason: 'socket-open'\}\)/.test(terminalBootSource), 'socket open starts a blank-screen risk window');
    assert.ok(/socket\.onmessage = event => \{[\s\S]*const firstOutput = item\.terminalOutputSeen !== true;[\s\S]*item\.fileUnderlineController\?\.schedule\?\.\(\{reason: 'output'\}\);[\s\S]*if \(firstOutput\) scheduleTerminalBlankScreenRefresh\(session, \{reason: 'first-output'\}\);[\s\S]*scheduleTerminalAttentionHighlight\(session\)/.test(terminalBootSource), 'terminal output only schedules a blank-screen probe for the first output after open while attention stays frame-coalesced');
    assert.equal(/socket\.onmessage = event => \{[\s\S]*scheduleTerminalBlankScreenRefresh\(session\);\s*[\s\S]*scheduleTerminalAttentionHighlight\(session\)/.test(terminalBootSource), false, 'terminal output no longer runs the old unconditional blank-screen probe');
  });

  test('client sluggishness counters and hidden-panel gates share one instrumentation path', () => {
    const bootstrapSource = fs.readFileSync('static_src/js/yolomux/00_bootstrap_state.js', 'utf8');
    const coreSource = fs.readFileSync('static_src/js/yolomux/10_core_utils.js', 'utf8');
    const terminalBootSource = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
    const debugSource = fs.readFileSync('static_src/js/yolomux/83_debug_panel.js', 'utf8');
    const panelSource = fs.readFileSync('static_src/js/yolomux/78_panel_shell.js', 'utf8');
    const menuSource = fs.readFileSync('static_src/js/yolomux/30_app_menus.js', 'utf8');
    const activitySource = fs.readFileSync('static_src/js/yolomux/45_agent_window_activity.js', 'utf8');
    const tabberSource = fs.readFileSync('static_src/js/yolomux/40_file_explorer_files.js', 'utf8');
    const changesSource = fs.readFileSync('static_src/js/yolomux/90_changes_editor.js', 'utf8');
    assert.ok(/const clientPerfCounters = new Map\(\)/.test(bootstrapSource) && /function recordClientPerfCounter\(name, durationMs = null, details = \{\}\)/.test(coreSource), 'client perf counters have one shared owner');
    assert.ok(/function installClientPerfLongTaskObserver\(\)[\s\S]*PerformanceObserver[\s\S]*entryTypes: \['longtask'\]/.test(coreSource), 'client perf samples Long Task entries through the shared owner');
    assert.ok(/clientPerfMeasureSinceMark\('keydownToTermData'/.test(terminalBootSource) && /clientPerfStart\('wsSend'\)/.test(terminalBootSource) && /recordClientPerfCounter\('echoToTermWrite'/.test(terminalBootSource), 'terminal input latency records keydown, websocket send, and echo-to-write measurements');
    assert.ok(/clientPerfStart\('xtermWrite'\)/.test(terminalBootSource) && /clientPerfStart\('terminalUnderlineRender'\)/.test(coreSource), 'xterm writes and terminal underline renders are timed');
    assert.ok(/clientPerfStart\('terminalAttentionScan'\)/.test(fs.readFileSync('static_src/js/yolomux/70_layout_actions.js', 'utf8')) && /clientPerfStart\('terminalBlankProbe'\)/.test(fs.readFileSync('static_src/js/yolomux/70_layout_actions.js', 'utf8')), 'terminal attention and blank probes are timed');
    assert.ok(/function renderInfoPanel\(options = \{\}\)[\s\S]*!infoPanelRenderVisible\(\)[\s\S]*recordClientPerfCounter\('renderInfoPanel', 0, \{skipped: 1\}\)/.test(terminalBootSource), 'hidden YO!info render calls skip tree rebuilds and count the skipped work');
    assert.ok(/function renderInfoPanelMeasured\(node, options = \{\}\)[\s\S]*const syncInfoContent = \(\) =>[\s\S]*signature === infoPanelLastRenderSignature[\s\S]*const hasContent = Boolean\(node\.children\?\.length \|\| String\(node\.innerHTML \|\| ''\)\.trim\(\)\);[\s\S]*if \(!hasContent\) renderInfoContent\(infoPanelLastRenderHtml\);[\s\S]*else syncInfoContent\(\)/.test(terminalBootSource), 'unchanged YO!info renders preserve live anchors through pane-focus pointerdown while still restoring an empty cached panel');
    assert.ok(/function queueClientPushEvent\(type, payload = \{\}\)[\s\S]*requestAnimationFrame[\s\S]*flushQueuedClientPushEvents/.test(terminalBootSource), 'client SSE push handling coalesces to one frame');
    assert.ok(/function renderAutoApproveStatusSurfaces\(result = \{\}\)[\s\S]*clientPerfStart\('autoStatusRender'\)/.test(terminalBootSource), 'auto-status refresh renders through one measured batch');
    assert.ok(/function renderPanels\(previousActive = \[\], options = \{\}\)[\s\S]*clientPerfStart\('renderPanels'\)/.test(panelSource) && /function renderPaneTabStrips\(\)[\s\S]*clientPerfStart\('renderPaneTabStrips'\)/.test(panelSource), 'shared panel and tab-strip renderers are counted');
    assert.ok(/function renderSessionButtons\(options = \{\}\)[\s\S]*clientPerfStart\('renderSessionButtons'\)/.test(menuSource), 'topbar session-button renders are counted');
    assert.ok(/clientPerfStart\('finderRefresh'\)/.test(tabberSource) && /recordClientPerfCounter\('sessionFilesRefresh'/.test(changesSource) && /recordClientPerfCounter\('sessionFilesRender'/.test(changesSource), 'Finder and Differ conditional refresh paths count skipped work, changed roots, and rendered rows');
    assert.ok(/const debugModeExplicitUrlEnabled = urlFlagEnabled\('debug'\)/.test(bootstrapSource) && /function debugClientPerfHtml\(\)[\s\S]*debugModeExplicitUrlEnabled !== true[\s\S]*data-js-debug-client-perf/.test(debugSource) && /Client work counters:/.test(debugSource), 'YO!stats only displays client work counters for explicit debug=1 while still exporting them');
    assert.ok(/const animate = options\.animate !== false/.test(activitySource) && /agentWindowActivityIconHtmlForStatus\(agentStatusForIcon, agentKey, session, \{animate: false\}\)/.test(tabberSource), 'secondary status copies reuse the shared renderer in static mode');
  });

  // (no false merge): a fresh URL on the next flush-left row is its own link, not a continuation.
  test('t@7047', () => {
    const api = loadYolomux();
    const first = 'https://example.com/first';
    const second = 'https://example.com/second';
    const lines = [
      terminalLine(first, false),
      terminalLine(second, false),
    ];
    const term = {cols: first.length + 1, buffer: {active: {getLine: index => lines[index] || null}}};
    const row1 = api.terminalWrappedLineLinks(term, 1);
    const row2 = api.terminalWrappedLineLinks(term, 2);
    assert.equal(row1.length, 1, 'fresh-next-url row 1 has one link');
    assert.equal(row1[0].text, first, 'fresh-next-url row 1 stays separate');
    assert.equal(row1[0].range.end.y, 1, 'fresh-next-url row 1 underline does not continue');
    assert.equal(row2.length, 1, 'fresh-next-url row 2 has one link');
    assert.equal(row2[0].text, second, 'fresh-next-url row 2 stays separate');
    assert.equal(row2[0].range.start.y, 2, 'fresh-next-url row 2 starts on row 2');
  });

  test('attention prompt question highlights the visible terminal row', () => {
    const api = loadYolomux('', ['1']);
    api.setTranscriptInfoForTest('1', {agents: [{kind: 'claude'}], panes: []});
    const container = api.testElementForId('terminal-pane-1');
    container.className = 'terminal';
    container.rect = {left: 0, top: 0, width: 800, height: 120, right: 800, bottom: 120};
    const xtermRows = new TestElement('xterm-rows');
    xtermRows.className = 'xterm-rows';
    xtermRows.rect = {left: 0, top: 0, width: 800, height: 120, right: 800, bottom: 120};
    const questionText = 'What would you like to do?';
    const visibleRows = ['Claude Code', `>> ${questionText}`, '1. Proceed', '2. Cancel']
      .map((text, index) => {
        const row = new TestElement(`row-${index}`);
        row.textContent = text;
        row.rect = {left: 0, top: index * 20, width: 800, height: 20, right: 800, bottom: (index + 1) * 20};
        xtermRows.appendChild(row);
        return row;
      });
    container.appendChild(xtermRows);
    api.registerTerminalForTest('1', {
      cols: 80,
      rows: 6,
      _core: {_renderService: {dimensions: {css: {cell: {width: 10, height: 20}}}}},
      buffer: {active: {length: visibleRows.length, viewportY: 0, getLine: index => terminalLine(visibleRows[index]?.textContent || '')}},
    });
    api.setAutoApproveStateForTest('1', {
      enabled: true,
      screen: {key: 'needs-input', text: questionText, question_text: questionText},
      prompt: {visible: false},
    });

    assert.deepStrictEqual(canonical(api.terminalAttentionQuestionTextsForTest('1')), [questionText], 'question text comes from the same payload that drives attention');
    assert.equal(api.syncTerminalAttentionHighlightForTest('1'), true, 'attention state paints a question row');
    assert.equal(visibleRows[1].classList.contains('terminal-attention-question-row'), true, 'the exact visible question row is marked');
    const overlay = container.querySelector('.terminal-attention-question-overlay[data-session="1"]');
    assert.ok(overlay, 'a visible overlay is created for canvas-rendered terminals');
    assert.equal(container.querySelectorAll('.terminal-attention-question-overlay[data-session="1"]').length, 1, 'single-line prompts create one overlay segment');
    assert.equal(overlay.style.top, '20px', 'overlay is aligned to the question row');
    assert.equal(overlay.style.left, '30px', 'overlay starts at the matched sentence, not the start of the row');
    assert.equal(overlay.style.width, `${questionText.length * 10}px`, 'overlay width tracks the matched sentence, not the whole row');

    api.setAutoApproveStateForTest('1', {enabled: true, screen: {key: 'idle', text: ''}});
    assert.equal(api.syncTerminalAttentionHighlightForTest('1'), false, 'non-attention state clears the mark');
    assert.equal(visibleRows[1].classList.contains('terminal-attention-question-row'), false, 'cleared attention removes the row class');
    assert.equal(container.querySelector('.terminal-attention-question-overlay[data-session="1"]'), null, 'cleared attention removes the overlay');

    api.setAutoApproveStateForTest('1', {enabled: true, screen: {key: 'needs-input', text: 'waiting for input'}});
    visibleRows[1].textContent = 'Tip: this is not the prompt';
    visibleRows[3].textContent = 'Something something sentence something?';
    assert.equal(api.syncTerminalAttentionHighlightForTest('1'), false, 'generic attention state does not guess from a historical question-looking terminal row');
    assert.equal(visibleRows[3].classList.contains('terminal-attention-question-row'), false, 'a prior typed question is never presented as the current agent question');
  });

  test('attention shortcut footer hint is not highlighted as a question', () => {
    const api = loadYolomux('', ['1']);
    api.setTranscriptInfoForTest('1', {agents: [{kind: 'claude'}], panes: []});
    const container = api.testElementForId('terminal-pane-1');
    container.className = 'terminal';
    container.rect = {left: 0, top: 0, width: 800, height: 80, right: 800, bottom: 80};
    const xtermRows = new TestElement('xterm-shortcut-hint-rows');
    xtermRows.className = 'xterm-rows';
    xtermRows.rect = {left: 0, top: 0, width: 800, height: 80, right: 800, bottom: 80};
    const hintText = '? for shortcuts · ← for agents';
    const visibleRows = [hintText, ''].map((text, index) => {
      const row = new TestElement(`shortcut-hint-row-${index}`);
      row.textContent = text;
      row.rect = {left: 0, top: index * 20, width: 800, height: 20, right: 800, bottom: (index + 1) * 20};
      xtermRows.appendChild(row);
      return row;
    });
    container.appendChild(xtermRows);
    api.registerTerminalForTest('1', {
      cols: 80,
      rows: 4,
      _core: {_renderService: {dimensions: {css: {cell: {width: 10, height: 20}}}}},
      buffer: {active: {length: visibleRows.length, viewportY: 0, getLine: index => terminalLine(visibleRows[index]?.textContent || '')}},
    });
    api.setAutoApproveStateForTest('1', {
      enabled: true,
      screen: {key: 'needs-input', text: hintText, question_text: hintText},
      prompt: {visible: false},
    });

    assert.deepStrictEqual(canonical(api.terminalAttentionQuestionTextsForTest('1')), [], 'shortcut footer text is chrome, not attention question text');
    assert.equal(api.syncTerminalAttentionHighlightForTest('1'), false, 'shortcut footer text does not receive the red question overlay');
    assert.equal(visibleRows[0].classList.contains('terminal-attention-question-row'), false, 'shortcut footer row is left unmarked');
    assert.equal(container.querySelector('.terminal-attention-question-overlay[data-session="1"]'), null, 'shortcut footer row creates no overlay');
  });

  test('attention prompt question highlights wrapped sentence spans only', () => {
    const api = loadYolomux('', ['1']);
    api.setTranscriptInfoForTest('1', {agents: [{kind: 'claude'}], panes: []});
    const container = api.testElementForId('terminal-pane-1');
    container.className = 'terminal';
    container.rect = {left: 0, top: 0, width: 900, height: 140, right: 900, bottom: 140};
    const xtermRows = new TestElement('xterm-rows');
    xtermRows.className = 'xterm-rows';
    xtermRows.rect = {left: 0, top: 0, width: 900, height: 140, right: 900, bottom: 140};
    const questionText = 'Want me to draft a pending review on GitHub with the merge-order note, or leave it as-is in chat?';
    const firstQuestionRow = '>> Want me to draft a pending review on GitHub with the merge-order note, or leave it as-';
    const secondQuestionRow = "is in chat? (I won't submit anything publicly without you saying so.)";
    const visibleRows = ['Claude Code', firstQuestionRow, secondQuestionRow, '1. Draft locally', '2. Leave in chat']
      .map((text, index) => {
        const row = new TestElement(`wrapped-row-${index}`);
        row.textContent = text;
        row.rect = {left: 0, top: index * 20, width: 900, height: 20, right: 900, bottom: (index + 1) * 20};
        xtermRows.appendChild(row);
        return row;
      });
    container.appendChild(xtermRows);
    api.registerTerminalForTest('1', {
      cols: firstQuestionRow.length,
      rows: 8,
      _core: {_renderService: {dimensions: {css: {cell: {width: 10, height: 20}}}}},
      buffer: {active: {length: visibleRows.length, viewportY: 0, getLine: index => terminalLine(visibleRows[index]?.textContent || '')}},
    });
    api.setAutoApproveStateForTest('1', {
      enabled: true,
      screen: {key: 'needs-input', text: questionText, question_text: questionText},
      prompt: {visible: false},
    });

    assert.equal(api.syncTerminalAttentionHighlightForTest('1'), true, 'explicit wrapped question text paints the wrapped sentence');
    assert.equal(visibleRows[0].classList.contains('terminal-attention-question-row'), false, 'nearby header text is not marked');
    assert.equal(visibleRows[1].classList.contains('terminal-attention-question-row'), true, 'first wrapped question row is marked');
    assert.equal(visibleRows[2].classList.contains('terminal-attention-question-row'), true, 'second wrapped question row is marked');
    assert.equal(visibleRows[3].classList.contains('terminal-attention-question-row'), false, 'nearby option text is not marked');
    const overlays = container.querySelectorAll('.terminal-attention-question-overlay[data-session="1"]');
    assert.equal(overlays.length, 2, 'wrapped question creates one overlay segment per visual row');
    assert.deepStrictEqual(canonical(overlays.map(overlay => ({
      top: overlay.style.top,
      left: overlay.style.left,
      width: overlay.style.width,
    }))), [
      {top: '20px', left: '30px', width: `${(firstQuestionRow.length - 3) * 10}px`},
      {top: '40px', left: '0px', width: `${'is in chat?'.length * 10}px`},
    ], 'wrapped overlays cover only the question sentence and stop before the parenthetical');

    api.setAutoApproveStateForTest('1', {enabled: true, screen: {key: 'idle', text: ''}});
    assert.equal(api.syncTerminalAttentionHighlightForTest('1'), false, 'clearing state removes wrapped overlays');
    assert.equal(container.querySelectorAll('.terminal-attention-question-overlay[data-session="1"]').length, 0, 'all wrapped overlay segments are removed');
  });

  test('attention highlight requires an explicit question payload', () => {
    const api = loadYolomux('', ['1']);
    api.setTranscriptInfoForTest('1', {agents: [{kind: 'claude'}], panes: []});
    const container = api.testElementForId('terminal-pane-1');
    container.className = 'terminal';
    container.rect = {left: 0, top: 0, width: 900, height: 140, right: 900, bottom: 140};
    const xtermRows = new TestElement('xterm-rows');
    xtermRows.className = 'xterm-rows';
    xtermRows.rect = {left: 0, top: 0, width: 900, height: 140, right: 900, bottom: 140};
    const firstQuestionRow = '>> Want me to draft a pending review on GitHub with the merge-order note, or leave it as-';
    const secondQuestionRow = "is in chat? (I won't submit anything publicly without you saying so.)";
    const visibleRows = ['Claude Code', firstQuestionRow, secondQuestionRow, 'Tip: this is not a question']
      .map((text, index) => {
        const row = new TestElement(`fallback-wrapped-row-${index}`);
        row.textContent = text;
        row.rect = {left: 0, top: index * 20, width: 900, height: 20, right: 900, bottom: (index + 1) * 20};
        xtermRows.appendChild(row);
        return row;
      });
    container.appendChild(xtermRows);
    api.registerTerminalForTest('1', {
      cols: firstQuestionRow.length,
      rows: 8,
      _core: {_renderService: {dimensions: {css: {cell: {width: 10, height: 20}}}}},
      buffer: {active: {length: visibleRows.length, viewportY: 0, getLine: index => terminalLine(visibleRows[index]?.textContent || '')}},
    });
    api.setAutoApproveStateForTest('1', {enabled: true, screen: {key: 'needs-input', text: 'waiting for input'}});

    assert.equal(api.syncTerminalAttentionHighlightForTest('1'), false, 'generic attention state does not infer a question from terminal history');
    assert.equal(visibleRows[0].classList.contains('terminal-attention-question-row'), false, 'generic attention leaves header text unmarked');
    assert.equal(visibleRows[1].classList.contains('terminal-attention-question-row'), false, 'generic attention does not mark a prior wrapped question');
    assert.equal(visibleRows[2].classList.contains('terminal-attention-question-row'), false, 'generic attention does not mark continuation text');
    assert.equal(visibleRows[3].classList.contains('terminal-attention-question-row'), false, 'generic attention leaves nearby text unmarked');
    const overlays = container.querySelectorAll('.terminal-attention-question-overlay[data-session="1"]');
    assert.equal(overlays.length, 0, 'generic attention creates no red question overlay');
  });

  test('attention highlight uses the latest visible copy of the explicit question', () => {
    const api = loadYolomux('', ['1']);
    api.setTranscriptInfoForTest('1', {agents: [{kind: 'claude'}], panes: []});
    const container = api.testElementForId('terminal-pane-1');
    container.className = 'terminal';
    container.rect = {left: 0, top: 0, width: 900, height: 100, right: 900, bottom: 100};
    const xtermRows = new TestElement('xterm-duplicate-question-rows');
    xtermRows.className = 'xterm-rows';
    xtermRows.rect = {left: 0, top: 0, width: 900, height: 100, right: 900, bottom: 100};
    const questionText = 'Should I continue with the fix?';
    const visibleRows = [`❯ ${questionText}`, 'Earlier terminal output', `Claude: ${questionText}`, '1. Continue', '2. Stop']
      .map((text, index) => {
        const row = new TestElement(`duplicate-question-row-${index}`);
        row.textContent = text;
        row.rect = {left: 0, top: index * 20, width: 900, height: 20, right: 900, bottom: (index + 1) * 20};
        xtermRows.appendChild(row);
        return row;
      });
    container.appendChild(xtermRows);
    api.registerTerminalForTest('1', {
      cols: 90,
      rows: 5,
      _core: {_renderService: {dimensions: {css: {cell: {width: 10, height: 20}}}}},
      buffer: {active: {length: visibleRows.length, viewportY: 0, getLine: index => terminalLine(visibleRows[index]?.textContent || '')}},
    });
    api.setAutoApproveStateForTest('1', {enabled: true, screen: {key: 'needs-input', text: questionText, question_text: questionText}});

    assert.equal(api.syncTerminalAttentionHighlightForTest('1'), true, 'explicit question payload paints a terminal overlay');
    assert.equal(visibleRows[0].classList.contains('terminal-attention-question-row'), false, 'stale earlier copy of the question stays unmarked');
    assert.equal(visibleRows[2].classList.contains('terminal-attention-question-row'), true, 'latest visible prompt copy is marked');
  });

  test('attention prompt fragment expands to the full visible question sentence', () => {
    const api = loadYolomux('', ['1']);
    api.setTranscriptInfoForTest('1', {agents: [{kind: 'claude'}], panes: []});
    const container = api.testElementForId('terminal-pane-1');
    container.className = 'terminal';
    container.rect = {left: 0, top: 0, width: 1600, height: 160, right: 1600, bottom: 160};
    const xtermRows = new TestElement('xterm-fragment-rows');
    xtermRows.className = 'xterm-rows';
    xtermRows.rect = {left: 0, top: 0, width: 1600, height: 160, right: 1600, bottom: 160};
    const firstQuestionRow = 'is published" notice, so its POC status is consistent — want me to also add an explicit "real after #10851';
    const secondQuestionRow = 'lands" line to #10853, or is the #10851 note enough?';
    const visibleRows = [
      'That captures all three points: latest version after #53/#72 land, #10851 = next priority, and #10853 =',
      'POC → real after #10851. #10853 already carries its own "WIP — DOES NOT PASS CI until the frontend-crate',
      firstQuestionRow,
      secondQuestionRow,
      '* Churned for 1m 8s',
    ].map((text, index) => {
      const row = new TestElement(`fragment-question-row-${index}`);
      row.textContent = text;
      row.rect = {left: 0, top: index * 20, width: 1600, height: 20, right: 1600, bottom: (index + 1) * 20};
      xtermRows.appendChild(row);
      return row;
    });
    container.appendChild(xtermRows);
    api.registerTerminalForTest('1', {
      cols: firstQuestionRow.length,
      rows: 8,
      _core: {_renderService: {dimensions: {css: {cell: {width: 10, height: 20}}}}},
      buffer: {active: {length: visibleRows.length, viewportY: 0, getLine: index => terminalLine(visibleRows[index]?.textContent || '')}},
    });
    api.setAutoApproveStateForTest('1', {
      enabled: true,
      screen: {key: 'needs-input', text: secondQuestionRow, question_text: secondQuestionRow},
      prompt: {visible: false},
    });

    assert.equal(api.syncTerminalAttentionHighlightForTest('1'), true, 'partial wrapped question payload still paints the full visible question');
    assert.equal(visibleRows[0].classList.contains('terminal-attention-question-row'), false, 'prior explanation row is not marked');
    assert.equal(visibleRows[1].classList.contains('terminal-attention-question-row'), false, 'prior sentence row is not marked');
    assert.equal(visibleRows[2].classList.contains('terminal-attention-question-row'), true, 'highlight starts at the want-me sentence row');
    assert.equal(visibleRows[3].classList.contains('terminal-attention-question-row'), true, 'highlight includes the suffix row from the payload');
    assert.equal(visibleRows[4].classList.contains('terminal-attention-question-row'), false, 'later status row is not marked');
    const overlays = container.querySelectorAll('.terminal-attention-question-overlay[data-session="1"]');
    const start = firstQuestionRow.indexOf('want me');
    assert.equal(overlays.length, 2, 'expanded fragment creates one overlay per visual question row');
    assert.deepStrictEqual(canonical(overlays.map(overlay => ({
      top: overlay.style.top,
      left: overlay.style.left,
      width: overlay.style.width,
    }))), [
      {top: '40px', left: `${start * 10}px`, width: `${(firstQuestionRow.length - start) * 10}px`},
      {top: '60px', left: '0px', width: `${secondQuestionRow.length * 10}px`},
    ], 'overlay expands backward to the full visible question sentence');
  });

  test('Question/QUES? prompt suffix after version token expands to the full visible question sentence', () => {
    const api = loadYolomux('', ['1']);
    api.setTranscriptInfoForTest('1', {agents: [{kind: 'claude'}], panes: []});
    const container = api.testElementForId('terminal-pane-1');
    container.className = 'terminal';
    container.rect = {left: 0, top: 0, width: 1900, height: 100, right: 1900, bottom: 100};
    const xtermRows = new TestElement('xterm-version-question-rows');
    xtermRows.className = 'xterm-rows';
    xtermRows.rect = {left: 0, top: 0, width: 1900, height: 100, right: 1900, bottom: 100};
    const firstRow = "Net: dynamo and our vLLM/SGLang fixtures agree at 0.23.0 / 0.5.12.post1. Only gpt-oss/harmony's vLLM";
    const questionText = 'Want me to take on the harmony token-id recapture to close that last 0.22.x, or leave it?';
    const secondRow = `stamp lags. ${questionText}`;
    const visibleRows = [firstRow, secondRow, '1. Yes', '2. No'].map((text, index) => {
      const row = new TestElement(`version-question-row-${index}`);
      row.textContent = text;
      row.rect = {left: 0, top: index * 20, width: 1900, height: 20, right: 1900, bottom: (index + 1) * 20};
      xtermRows.appendChild(row);
      return row;
    });
    container.appendChild(xtermRows);
    api.registerTerminalForTest('1', {
      cols: firstRow.length,
      rows: 6,
      _core: {_renderService: {dimensions: {css: {cell: {width: 10, height: 20}}}}},
      buffer: {active: {length: visibleRows.length, viewportY: 0, getLine: index => terminalLine(visibleRows[index]?.textContent || '')}},
    });
    api.setAutoApproveStateForTest('1', {
      enabled: true,
      screen: {key: 'needs-input', text: 'x, or leave it?', question_text: 'x, or leave it?'},
      prompt: {visible: false},
    });

    assert.equal(api.syncTerminalAttentionHighlightForTest('1'), true, 'suffix-only question payload paints the visible question sentence');
    assert.equal(visibleRows[0].classList.contains('terminal-attention-question-row'), false, 'prior wrapped row is not marked');
    assert.equal(visibleRows[1].classList.contains('terminal-attention-question-row'), true, 'question row is marked');
    assert.equal(visibleRows[2].classList.contains('terminal-attention-question-row'), false, 'option rows are not marked');
    const overlays = container.querySelectorAll('.terminal-attention-question-overlay[data-session="1"]');
    const questionStart = secondRow.indexOf('Want me');
    assert.equal(overlays.length, 1, 'same-row suffix expansion stays a single overlay');
    assert.deepStrictEqual(canonical(overlays.map(overlay => ({
      top: overlay.style.top,
      left: overlay.style.left,
      width: overlay.style.width,
    }))), [
      {top: '20px', left: `${questionStart * 10}px`, width: `${questionText.length * 10}px`},
    ], 'version-like dots do not split the question sentence');
  });

  test('attention note-prefixed wrapped question highlights the entire sentence', () => {
    const api = loadYolomux('', ['1']);
    api.setTranscriptInfoForTest('1', {agents: [{kind: 'claude'}], panes: []});
    const container = api.testElementForId('terminal-pane-1');
    container.className = 'terminal';
    container.rect = {left: 0, top: 0, width: 1900, height: 100, right: 1900, bottom: 100};
    const xtermRows = new TestElement('xterm-note-question-rows');
    xtermRows.className = 'xterm-rows';
    xtermRows.rect = {left: 0, top: 0, width: 1900, height: 100, right: 1900, bottom: 100};
    const firstRow = 'Note: the scratch sglang-localdev container + ~/dynamo/vllm-0.23.0 worktree are still up from the capture';
    const secondRow = 'work — want me to tear those down now that both PRs are in?';
    const questionText = 'want me to tear those down now that both PRs are in?';
    const visibleRows = [firstRow, secondRow, '1. Yes', '2. No'].map((text, index) => {
      const row = new TestElement(`note-question-row-${index}`);
      row.textContent = text;
      row.rect = {left: 0, top: index * 20, width: 1900, height: 20, right: 1900, bottom: (index + 1) * 20};
      xtermRows.appendChild(row);
      return row;
    });
    container.appendChild(xtermRows);
    api.registerTerminalForTest('1', {
      cols: firstRow.length,
      rows: 6,
      _core: {_renderService: {dimensions: {css: {cell: {width: 10, height: 20}}}}},
      buffer: {active: {length: visibleRows.length, viewportY: 0, getLine: index => terminalLine(visibleRows[index]?.textContent || '')}},
    });
    api.setAutoApproveStateForTest('1', {
      enabled: true,
      screen: {key: 'needs-input', text: questionText, question_text: questionText},
      prompt: {visible: false},
    });

    assert.equal(api.syncTerminalAttentionHighlightForTest('1'), true, 'note-prefixed question payload paints the whole visible sentence');
    assert.equal(visibleRows[0].classList.contains('terminal-attention-question-row'), true, 'the leading Note row is part of the highlighted sentence');
    assert.equal(visibleRows[1].classList.contains('terminal-attention-question-row'), true, 'the wrapped question row is highlighted too');
    assert.equal(visibleRows[2].classList.contains('terminal-attention-question-row'), false, 'option rows are not marked');
    const overlays = container.querySelectorAll('.terminal-attention-question-overlay[data-session="1"]');
    assert.deepStrictEqual(canonical(overlays.map(overlay => ({
      top: overlay.style.top,
      left: overlay.style.left,
      width: overlay.style.width,
    }))), [
      {top: '0px', left: '0px', width: `${firstRow.length * 10}px`},
      {top: '20px', left: '0px', width: `${secondRow.length * 10}px`},
    ], 'overlay expands backward through the Note prefix and covers the full wrapped sentence');
  });

  // (no false merge): even an unterminated URL-looking row cannot absorb a flush-left continuation when
  // it did not reach the terminal edge.
  test('t@7052', () => {
    const api = loadYolomux();
    const lines = [
      terminalLine('https://example.com/abc', false),
      terminalLine('def', false),
    ];
    const term = {cols: 80, buffer: {active: {getLine: index => lines[index] || null}}};
    const row1 = api.terminalWrappedLineLinks(term, 1);
    const row2 = api.terminalWrappedLineLinks(term, 2);
    assert.equal(row1.length, 1, 'short-edge row 1 has its own link');
    assert.equal(row1[0].text, 'https://example.com/abc', 'short-edge row 1 does not absorb row 2');
    assert.equal(row1[0].range.end.y, 1, 'short-edge row 1 underline stays on row 1');
    assert.equal(row2.length, 0, 'short-edge row 2 has no standalone URL');
  });

  // (no false JOIN): a COMPLETE url at end-of-line that ends well short of the terminal's
  // right edge was NOT clipped, so the indented next row must stay independent — earlier this merged into
  // one bogus link `https://example.comnext step`.
  test('t@7057', () => {
    const api = loadYolomux();
    const lines = [
      terminalLine('See https://example.com'),  // complete URL, ends at col 23 of an 80-col terminal
      terminalLine('    next step', false),      // indented prose — NOT a clipped URL continuation
    ];
    const term = {cols: 80, buffer: {active: {getLine: index => lines[index] || null}}};
    const row1 = api.terminalWrappedLineLinks(term, 1);
    assert.equal(row1.length, 1, 'C1: a complete URL at EOL links only itself');
    assert.equal(row1[0].text, 'https://example.com', 'C1: link text is the complete URL, not joined with the next row');
    assert.equal(row1[0].range.end.y, 1, 'C1: the underline stays on the URL row (no false continuation onto row 2)');
    const row2 = api.terminalWrappedLineLinks(term, 2);
    assert.equal(row2.length, 0, 'C1: the indented prose continuation is not a link');
  });

  // (no false merge): an indented line under a row that ends in PROSE (not an unterminated URL)
  // is left alone — only a url token that runs off the right edge gets a continuation stitched on.
  test('t@7074', () => {
    const api = loadYolomux();
    const lines = [
      terminalLine('Here are the steps to run:'),  // ends in prose, no trailing URL
      terminalLine('    https://example.com/guide', false),
    ];
    const term = {buffer: {active: {getLine: index => lines[index] || null}}};
    const row1 = api.terminalWrappedLineLinks(term, 1);
    assert.equal(row1.length, 0, 'a prose line is not merged with the indented URL below it');
    const row2 = api.terminalWrappedLineLinks(term, 2);
    assert.equal(row2.length, 1, 'the indented URL on its own row still links');
    assert.equal(row2[0].text, 'https://example.com/guide');
    // It links at its own indented columns (4-space indent → starts at column 5).
    assert.equal(row2[0].range.start.x, 5, 'a standalone indented URL underlines at its real column');
    assert.equal(row2[0].range.start.y, 2);
  });

  // watched-PR ref normalization (client mirror of the backend parse_pull_request_ref).
  test('t@7092', () => {
    const api = loadYolomux();
    assert.equal(api.normalizeWatchedPrRef('ai-dynamo/frontend-crates#18'), 'ai-dynamo/frontend-crates#18');
    assert.equal(api.normalizeWatchedPrRef('ai-dynamo/frontend-crates/18'), 'ai-dynamo/frontend-crates#18');
    assert.equal(api.normalizeWatchedPrRef('https://github.com/ai-dynamo/frontend-crates/pull/18'), 'ai-dynamo/frontend-crates#18');
    assert.equal(api.normalizeWatchedPrRef('https://github.com/owner/repo/pull/7/files'), 'owner/repo#7');
    assert.equal(api.normalizeWatchedPrRef('  owner/repo#7  '), 'owner/repo#7');
    assert.equal(api.normalizeWatchedPrRef('https://gitlab.com/owner/repo/pull/7'), '', 'non-github URL is rejected');
    assert.equal(api.normalizeWatchedPrRef('owner/repo'), '', 'a repo without a PR number is rejected');
    assert.equal(api.normalizeWatchedPrRef('owner/repo#0'), '', 'PR #0 is rejected');
    assert.equal(api.normalizeWatchedPrRef('not a ref'), '');
    assert.equal(api.normalizeWatchedPrRef('https://github.com/owner/repo/issues/3'), '', 'an issue URL is not a PR');
  });

  // watched-PR status snapshot + the pure transition detector (merge / CI→failing / review).
  test('t@7107', () => {
    const api = loadYolomux();
    const open = {state: 'open', checks: {state: 'passing'}, review_decision: 'REVIEW_REQUIRED'};
    assert.deepStrictEqual(canonical(api.watchedPrStatusSnapshot(open)), {merged: false, ci: 'passing', review: 'REVIEW_REQUIRED'});
    assert.equal(api.watchedPrStatusSnapshot({merged: true}).merged, true, 'merged flag → merged snapshot');
    assert.equal(api.watchedPrStatusSnapshot({status_label: 'merged'}).merged, true, 'merged status_label → merged snapshot');
    // First sighting (no prev) records a baseline → no transition (avoids a load-time storm).
    assert.deepStrictEqual(canonical(api.watchedPrTransitionKeys(null, api.watchedPrStatusSnapshot(open))), []);
    assert.deepStrictEqual(canonical(api.watchedPrTransitionKeys({merged: false, ci: 'passing', review: ''}, {merged: true, ci: 'passing', review: ''})), ['pr-merged'], '→ merged fires pr-merged');
    assert.deepStrictEqual(canonical(api.watchedPrTransitionKeys({merged: false, ci: 'passing', review: ''}, {merged: false, ci: 'failing', review: ''})), ['pr-ci-failing'], 'CI → failing fires pr-ci-failing');
    assert.deepStrictEqual(canonical(api.watchedPrTransitionKeys({merged: false, ci: 'failing', review: ''}, {merged: false, ci: 'passing', review: ''})), [], 'CI failing → passing is not a pr-ci-failing transition');
    assert.deepStrictEqual(canonical(api.watchedPrTransitionKeys({merged: false, ci: 'passing', review: 'REVIEW_REQUIRED'}, {merged: false, ci: 'passing', review: 'APPROVED'})), ['pr-review'], 'a review-decision change fires pr-review');
    const same = api.watchedPrStatusSnapshot(open);
    assert.deepStrictEqual(canonical(api.watchedPrTransitionKeys(same, same)), [], 'an unchanged snapshot fires nothing');
  });

  // notify_transitions gates the new PR keys — they are opt-in (NOT in the default allowlist).
  test('t@7124', () => {
    const api = loadYolomux();
    assert.equal(api.shouldNotifyTransitionKey('needs-input'), true, 'a default session-state key still notifies');
    assert.equal(api.shouldNotifyTransitionKey('pr-merged'), false, 'pr-merged is opt-in, off by default');
  });

  test('state contract keys route through STATE_KEY owners', () => {
    const bootSrc = fs.readFileSync('static_src/js/yolomux/00_bootstrap_state.js', 'utf8');
    assert.ok(/const STATE_KEY = Object\.freeze\(\{[\s\S]*needsApproval: 'needs-approval'[\s\S]*needsInput: 'needs-input'[\s\S]*working: 'working'[\s\S]*idle: 'idle'/.test(bootSrc), 'STATE_KEY owns the agent/session state-key contract');
    assert.ok(/const STATE_CLASS = Object\.freeze\(\{[\s\S]*needsInput: STATE_KEY\.needsInput[\s\S]*needsInputPane: `\$\{STATE_KEY\.needsInput\}-pane`/.test(bootSrc), 'STATE_CLASS derives state-backed CSS classes from STATE_KEY');
    const auditedSource = [
      'static_src/js/yolomux/20_layout_state.js',
      'static_src/js/yolomux/45_agent_window_activity.js',
      'static_src/js/yolomux/60_popovers_tabs.js',
      'static_src/js/yolomux/70_layout_actions.js',
      'static_src/js/yolomux/99_terminal_boot.js',
    ].map(path => fs.readFileSync(path, 'utf8')).join('\n');
    const contractKeys = '(approval|blocked|interrupted|needs-approval|needs-input|working|idle)';
    const equalitySubjects = '(screenKey|promptAttentionKey|key|stateKey|previous\\.state|state\\.key)';
    const rawLiteralChecks = [
      new RegExp(`classList\\??\\.\\s*(?:add|remove|toggle|contains)\\s*\\(\\s*['"](?:approval|blocked|interrupted|needs-approval|needs-input|idle)['"]`),
      new RegExp(`\\b${equalitySubjects}\\s*={2,3}\\s*['"]${contractKeys}['"]`),
      new RegExp(`['"]${contractKeys}['"]\\s*={2,3}\\s*\\b${equalitySubjects}\\b`),
      new RegExp(`stateValue\\(\\s*['"]${contractKeys}['"]`),
    ];
    for (const pattern of rawLiteralChecks) {
      assert.equal(pattern.test(auditedSource), false, `state-key literals must route through STATE_KEY/STATE_CLASS: ${pattern}`);
    }
  });

  // watched PRs have an initial fetch, SSE updates, container, and transition notifications.
  test('t@7131', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.equal(source.includes("resetRuntimeInterval('watched-prs', refreshWatchedPrs"), false, 'watched PRs no longer run a recurring browser poll');
    assert.ok(source.includes("apiFetchJson('/api/watched-prs')"), 'refreshWatchedPrs keeps the boot/manual watched-PR endpoint fetch');
    assert.equal(source.includes('id="info-watched"'), false, 'old YO!info watched-PR table container is removed');
    assert.ok(source.includes('notifyWatchedPrTransitions(watchedPrsData.watched_prs)'), 'incoming snapshots diff statuses to fire transition notifications');
  });

  // Dev-velocity #1b: in --dev mode the page subscribes to /api/dev-reload and reloads on bundle change.
  test('t@7140', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(source.includes('const devMode = bootstrap.dev === true'), 'the client reads the dev flag from the bootstrap');
    assert.ok(source.includes('new EventSource(`/api/dev-reload?bundle_revision=${revision}`)'), 'dev mode identifies its bundle on the dev-reload SSE channel');
    assert.ok(/addEventListener\('ready',[\s\S]{0,600}bootstrap\.devBundleRevision[\s\S]{0,220}location\.reload\(\)/.test(source), 'a dev-reload ready event refreshes a browser whose bundle predates a server restart');
    assert.ok(/addEventListener\('reload',[\s\S]{0,120}location\.reload\(\)/.test(source), 'a reload event reloads the page');
    assert.ok(source.includes('installDevAutoReload()'), 'the dev auto-reload is installed at boot');
  });

  // browser clients subscribe to server push events for the expensive live datasets.
  test('t@7149', () => {
    const api = loadYolomux();
    const ownerPayload = {
      generation: {hostname: 'devhost', port: 8002, project_root: '/home/keivenc/yolomux.dev8002', pid: 111},
      current_owner: {hostname: 'devhost', port: 8002, project_root: '/home/keivenc/yolomux.dev8002', pid: 111},
      roles: {
        'search-index': {owner: true, status: 'owner'},
        'stats-sampler': {owner: true, status: 'owner'},
      },
      search_index: {
        owner: true,
        status: 'owner',
        current_server: {hostname: 'devhost', port: 8002, project_root: '/home/keivenc/yolomux.dev8002', pid: 111},
        owner_server: {hostname: 'devhost', port: 8002, project_root: '/home/keivenc/yolomux.dev8002', pid: 111},
      },
    };
    const readerPayload = {
      generation: {hostname: 'devhost', port: 8003, project_root: '/home/keivenc/yolomux.dev8003', pid: 222},
      current_owner: {hostname: 'devhost', port: 8002, project_root: '/home/keivenc/yolomux.dev8002', pid: 111},
      roles: {
        'search-index': {owner: false, status: 'follower'},
        'stats-sampler': {owner: false, status: 'follower'},
      },
      search_index: {
        owner: false,
        status: 'follower',
        current_server: {hostname: 'devhost', port: 8003, project_root: '/home/keivenc/yolomux.dev8003', pid: 222},
        owner_server: {hostname: 'devhost', port: 8002, project_root: '/home/keivenc/yolomux.dev8002', pid: 111},
      },
    };
    assert.equal(api.backgroundOwnerSearchIndexSummaryForTest(ownerPayload).mode, 'leader', 'background-owner summary names the connected indexing leader');
    assert.equal(api.backgroundOwnerSearchIndexSummaryForTest(readerPayload).mode, 'follower', 'background-owner summary names a search-index follower');
    assert.equal(api.backgroundOwnerStatsSummaryForTest(ownerPayload).mode, 'leader', 'background-owner summary names the connected YO!stats leader');
    assert.equal(api.backgroundOwnerStatsSummaryForTest(readerPayload).mode, 'follower', 'background-owner summary names a YO!stats follower');
    assert.equal(api.backgroundOwnerSessionFilesSummaryForTest(readerPayload).mode, 'follower', 'background-owner summary names a session-files follower');
    api.setBackgroundOwnerStatusPayloadForTest({
      ...readerPayload,
      roles: {
        'search-index': {owner: true, status: 'owner'},
        'stats-sampler': {owner: false, status: 'follower'},
        'session-files': {owner: false, status: 'follower'},
      },
      search_index: {...readerPayload.search_index, owner: true, current_server: readerPayload.generation, owner_server: readerPayload.generation},
    });
    const topbarOwnerHtml = api.topbarOwnerStatusHtmlForTest();
    assert.ok(topbarOwnerHtml.includes('topbar-owner-status-shared') && topbarOwnerHtml.includes('IDX|STATS|SESS') && topbarOwnerHtml.includes('follower'), 'topbar owner chip shows shared background follower status');
    assert.ok(api.topbarOwnerStatusTitleForTest(api.backgroundOwnerSearchIndexSummaryForTest(readerPayload), api.backgroundOwnerStatsSummaryForTest(readerPayload), api.backgroundOwnerSessionFilesSummaryForTest(readerPayload)).includes('STATS leader: devhost:8002'), 'topbar title names the YO!stats leader');
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(source.includes("new EventSource('/api/client-events')"), 'client subscribes to the general server event stream');
    assert.ok(source.includes("installRuntimeIntervals();") && source.includes("installClientEventStream();"), 'SSE is installed alongside the remaining local ping/log timers');
    assert.equal(source.includes('function clientPushSuppressesPolling()'), false, 'expensive client polling gate is removed');
    assert.equal(source.includes('refreshTranscriptsFromRuntime'), false, 'metadata fallback poll wrapper is removed');
    assert.equal(source.includes('refreshWatchedFilesystemFromRuntime'), false, 'filesystem fallback poll wrapper is removed');
    assert.equal(source.includes('refreshSettingsFromRuntime'), false, 'settings fallback poll wrapper is removed');
    assert.ok(source.includes('syncServerWatchRoots({renew: true})'), 'connected push mode renews watched roots without polling the filesystem');
    assert.ok(source.includes("apiFetch('/api/watch/roots'"), 'client registers watched roots for server-side SSE polling');
    assert.ok(source.includes('function clientServerWatchRoots()'), 'client derives watched directory roots from Finder/session-file state');
    assert.ok(/function visibleFileEditorWatchFiles\(\)[\s\S]*?activePaneItems\(\)/.test(source), 'client reports active visible editor files separately from directory roots');
    assert.ok(/function backgroundFileEditorWatchFiles\(\)[\s\S]*?paneItems\(\)[\s\S]*?!visible\.has\(path\)/.test(source), 'client reports background editor files separately from active visible editor files');
    assert.ok(source.includes('files: visibleFileEditorWatchFiles()'), 'watch state includes visible editor file paths for the fast files_changed stream');
    assert.ok(source.includes('background_files: backgroundFileEditorWatchFiles()'), 'watch state includes background editor file paths for the slower files_changed stream');
    assert.ok(/function transcriptPreviewPaneIsActive\(session\)[\s\S]*pane\?\.classList\?\.contains\(CLS\.active\)[\s\S]*preview\?\.isConnected/.test(source), 'transcript context previews only subscribe when their transcript pane is active');
    assert.ok(/function transcriptContextWatchRequests\(\)[\s\S]*activeSessions[\s\S]*filter\(transcriptPreviewPaneIsActive\)[\s\S]*messages: transcriptPreviewMessages/.test(source), 'watch state derives context-item requests from visible transcript previews');
    assert.ok(source.includes("['settings_changed', 'attention_acks_changed', 'auto_approve_changed', 'background_owner_changed', 'background_refresh_done', 'tmux_signals_changed', 'watched_prs_changed', 'files_changed', 'fs_changed', 'session_files_ready', 'transcripts_changed', 'context_items_ready', 'activity_summary_ready', 'update_available', 'yoagent_conversation_changed', 'yoagent_jobs_changed', 'yoagent_skills_changed', 'yoagent_stream_delta']"), 'client listens for the expected push event types');
    assert.ok(/if \(type === 'attention_acks_changed'\)[\s\S]{0,120}applyAttentionAcknowledgementResponse\(payload\)/.test(source), 'attention acknowledgement pushes apply scoped key patches without refetching every session status');
    assert.ok(/addEventListener\('ready',[\s\S]{0,360}refreshAutoStatuses\(\)\.catch/.test(source), 'client-events ready re-fetches auto status so stale YO markers are backfilled after reconnect');
    assert.ok(/addEventListener\('ready',[\s\S]{0,520}refreshBackgroundOwnerStatus\(\{force: true\}\)\.catch/.test(source), 'client-events ready re-fetches background owner status after reconnect');
    assert.ok(/function installReconnectResyncHandlers\(\)[\s\S]*document\.addEventListener\('visibilitychange'[\s\S]*document\.visibilityState === 'visible'[\s\S]*scheduleReconnectResync\('visible'\)[\s\S]*window\.addEventListener\('online'[\s\S]*scheduleReconnectResync\('online'\)/.test(source), 'page wake and network restore schedule a shared refreshAll resync');
    assert.ok(/function scheduleReconnectResync\(reason = ''\)[\s\S]*setTimeout\(\(\) => \{[\s\S]*refreshAll\(\)/.test(source), 'wake/network reconnect resync is debounced before refreshAll');
    const runtimeSrc = fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8');
    assert.ok(runtimeSrc.includes("resetRuntimeInterval('auto-approve', () => {\n    if (clientEventsConnected === true) return null;\n    return refreshAutoStatuses();\n  }, autoApproveDisconnectedPollMs);"), 'auto-approve fallback poll only runs while client-events is disconnected');
    assert.ok(/if \(type === 'settings_changed'\)[\s\S]{0,220}applySettingsPayload\(payload\.data, \{force: true\}\)/.test(source), 'settings_changed applies direct payloads without polling settings again');
    assert.ok(/if \(type === 'auto_approve_changed'\)[\s\S]{0,120}applyAutoApprovePayload\(payload\.data\)/.test(source), 'auto_approve_changed applies direct payloads');
    assert.ok(/if \(type === 'background_owner_changed'\)[\s\S]{0,180}applyBackgroundOwnerStatusPayload\(payload\)/.test(source), 'background_owner_changed applies direct owner status');
    assert.ok(/if \(type === 'background_refresh_done'\)[\s\S]{0,180}payload\.role === 'search-index'[\s\S]{0,160}refreshBackgroundOwnerStatus\(\{force: true\}\)/.test(source), 'search-index refresh completion refreshes owner status');
    assert.ok(/payload\.role === 'search-index'[\s\S]{0,420}commandPaletteEffectiveMode\(\) === 'files'[\s\S]{0,180}refreshFileQuickOpenCandidates\(commandPaletteQuery\)/.test(source), 'search-index refresh completion reruns an open file search against the rebuilt index');
    assert.ok(/if \(payload\.role === 'session-files'\)[\s\S]{0,260}fetchSessionFiles\(\{silent: true\}\)/.test(source), 'session-files refresh completion refetches the visible Differ payload from the follower cache');
    assert.ok(/if \(type === 'tmux_signals_changed'\)[\s\S]{0,120}applyTmuxSignalsPayload\(payload\)/.test(source), 'tmux_signals_changed applies direct payloads');
    assert.ok(/if \(type === 'watched_prs_changed'\)[\s\S]{0,120}applyWatchedPrsPayload\(payload\.data\)/.test(source), 'watched_prs_changed applies direct payloads');
    assert.ok(/if \(type === 'transcripts_changed'\)[\s\S]{0,220}applyTranscriptsPayload\(payload\.data, \{refreshAuto: false, refreshContext: false, refreshActivity: false\}\)/.test(source), 'transcripts_changed applies direct metadata payloads');
    assert.ok(/if \(type === 'context_items_ready'\)[\s\S]{0,160}applyContextItemsPayloadFromPush\(payload\.data/.test(source), 'context_items_ready applies direct context payloads');
    assert.ok(/if \(type === 'activity_summary_ready'\)[\s\S]{0,120}applyActivitySummaryPayloadFromPush\(payload\.data\)/.test(source), 'activity_summary_ready applies direct summary payloads');
    assert.ok(/if \(type === 'yoagent_skills_changed'\)[\s\S]{0,160}refreshActivitySummary\(\{force: true/.test(source), 'yoagent_skills_changed refreshes YO!agent context');
    assert.ok(/if \(type === 'yoagent_jobs_changed'\)[\s\S]*loadYoagentJobs\(\{force: true, silent: true, render: yoagentPanelIsActive\(\)[\s\S]*maybeNotifyYoagentJob\(payload\.notification/.test(source), 'yoagent_jobs_changed refreshes jobs and can notify from server-fired jobs');
    assert.ok(/if \(type === 'session_files_ready'\)[\s\S]{0,180}applySessionFilesPayloadFromPush\(payload\.data, payload\.request/.test(source), 'session_files_ready applies direct session-files payloads');
    assert.equal(source.includes('session_files_changed'), false, 'stale session_files_changed refetch event path is removed');
    assert.ok(/if \(type === 'files_changed'\)[\s\S]{0,180}refreshOpenFilesFromPush\(payload\)/.test(source), 'files_changed refreshes visible editor files without waiting for directory payloads');
    const filePushHelper = source.slice(source.indexOf('async function refreshOpenFilesFromPush'), source.indexOf('async function refreshFileExplorerFromPush'));
    assert.equal(filePushHelper.includes('fetchDirectory'), false, 'files_changed uses the server file signature directly, not a parent-directory listing');
    assert.equal(filePushHelper.includes('refreshOpenFilesIfChanged'), false, 'files_changed does not route through the directory-backed polling helper');
    assert.equal(source.includes('function scheduleSessionFilesPushRefresh()'), false, 'session-files push no longer triggers a client refetch helper');
    assert.ok(source.includes("apiFetchJson('/api/background/status'"), 'client fetches background-owner status for connected-server indicators');
    assert.ok(source.includes('createTopbarOwnerStatus()') && source.includes('updateTopbarOwnerStatus()'), 'topbar renders the connected-server owner indicator');
    assert.ok(source.includes("backgroundOwnerRoleSummary('stats-sampler'") && source.includes("backgroundOwnerRoleSummary('session-files'"), 'topbar owner indicator uses the shared stats-sampler and session-files roles');
    assert.equal(source.includes('function infoServerRoleHtml()'), false, 'YO!info does not render a server-role strip');
    assert.equal(source.includes('info-server-role'), false, 'YO!info server-role markup is removed');
    const watchRootsHelper = source.slice(source.indexOf('function clientServerWatchRoots()'), source.indexOf('function clientServerWatchState()'));
    assert.equal(watchRootsHelper.includes('openFiles.keys()'), false, 'open editor file dirs are not folded into the slower directory watch roots');
    assert.ok(/function applyLayoutSlots[\s\S]*?syncServerWatchRoots\(\)/.test(source), 'layout/tab changes immediately resync the server watch state');
    const fsPushHelper = source.slice(source.indexOf('async function refreshFileExplorerFromPush'), source.indexOf('function expandUserPath'));
    assert.equal(fsPushHelper.includes('fetchSessionFiles'), false, 'fs_changed refreshes Finder/open-file state without also fetching session-files');
    assert.ok(/if \(type === 'fs_changed'\)[\s\S]{0,180}refreshFileExplorerFromPush\(payload\)/.test(source), 'fs_changed refreshes Finder/open-file state through the shared push helper');
    const renameSource = fs.readFileSync('static_src/js/yolomux/45_file_explorer_actions.js', 'utf8');
    const indexSource = fs.readFileSync('static_src/js/yolomux/40_file_explorer_files.js', 'utf8');
    assert.ok(/async function renameFileTreePath\([\s\S]*apiFetchJson\('\/api\/fs\/rename'[\s\S]*markFileIndexRootsRefreshing\(payload\.reindex_roots\)/.test(renameSource), 'Finder rename marks every backend-invalidated index root as rebuilding');
    assert.ok(/function markFileIndexRootsRefreshing\(roots = \[\]\)[\s\S]*fileExplorerIndexStatus\.set\(normalized, 'building'\)[\s\S]*refreshFileIndexStatus\(normalized\)/.test(indexSource), 'renamed-path index refresh uses the shared index-status owner instead of a duplicate search cache');
    assert.ok(source.includes('function clientServerWatchState()'), 'client reports rich watched state, not only filesystem roots');
    assert.ok(source.includes('context_items: transcriptContextWatchRequests()'), 'watch state includes visible transcript context previews only');
    assert.ok(source.includes('state.session_files = clientSessionFilesWatchRequests()'), 'watch state includes the current session-files request');
    assert.ok(source.includes("recordJsDebugEvent('sse'"), 'SSE events are captured in JS Debug');
    assert.ok(source.includes('const backgroundOwnerContextMenu = createContextMenuController()'), 'topbar owner takeover menu uses the shared context-menu controller');
    assert.ok(/function topbarOwnerStatusSummaries\(payload = backgroundOwnerStatusPayload\)[\s\S]*backgroundOwnerSearchIndexSummary[\s\S]*backgroundOwnerStatsSummary[\s\S]*backgroundOwnerSessionFilesSummary/.test(source), 'topbar owner chip and menu share one summary owner for IDX/STATS/SESS state');
    assert.ok(/function backgroundOwnerOwnsAllRoles\(payload = backgroundOwnerStatusPayload\)[\s\S]*summaries\.every\(item => item\.ownsRole === true \|\| item\.ownsIndex === true\)/.test(source), 'topbar owner takeover detects already-leader state from the shared summaries');
    assert.ok(/function showBackgroundOwnerContextMenu\(event\)[\s\S]*appendContextMenuButton\(menu, t\('backgroundOwner\.takeOver'\)/.test(source) && /async function claimBackgroundOwnerLeader\(\)[\s\S]*apiFetchJson\('\/api\/background\/claim', \{method: 'POST'\}\)/.test(source), 'right-clicking the owner chip offers a shared-menu Take over as leader action wired to the claim API');
    assert.ok(/function backgroundOwnerCurrentOwnerLive\(payload = backgroundOwnerStatusPayload[\s\S]*last_heartbeat[\s\S]*<= 10/.test(source) && /window\.confirm\(message\)/.test(source), 'live owner takeover prompts before asking the current leader to step down');
  });

  await testAsync('background owner context menu claims follower leadership', async () => {
    const api = loadYolomux();
    const currentServer = {hostname: 'devhost', port: 8001, project_root: '/home/keivenc/yolomux.dev8001', pid: 101, generation_id: 'current-gen'};
    const ownerServer = {hostname: 'devhost', port: 8002, project_root: '/home/keivenc/yolomux.dev8002', pid: 202, generation_id: 'owner-gen', last_heartbeat: Date.now() / 1000};
    const followerPayload = {
      generation: currentServer,
      current_owner: ownerServer,
      latest_generation: {generation_id: 'owner-gen'},
      roles: {
        'search-index': {owner: false, status: 'follower'},
        'stats-sampler': {owner: false, status: 'follower'},
        'session-files': {owner: false, status: 'follower'},
      },
      search_index: {
        owner: false,
        status: 'follower',
        current_server: currentServer,
        owner_server: ownerServer,
      },
    };
    const leaderPayload = {
      ...followerPayload,
      current_owner: currentServer,
      latest_generation: {generation_id: 'current-gen'},
      roles: {
        'search-index': {owner: true, status: 'owner'},
        'stats-sampler': {owner: true, status: 'owner'},
        'session-files': {owner: true, status: 'owner'},
      },
      search_index: {
        owner: true,
        status: 'owner',
        current_server: currentServer,
        owner_server: currentServer,
      },
    };
    const menuEvent = () => ({
      target: api.testElementForId('body'),
      clientX: 33,
      clientY: 44,
      preventDefault() { this.defaultPrevented = true; },
      stopPropagation() { this.propagationStopped = true; },
    });
    const clickEvent = () => ({
      preventDefault() { this.defaultPrevented = true; },
      stopPropagation() { this.propagationStopped = true; },
    });
    const ownerMenu = () => api.testElementForId('appOverlayRoot').children.find(child => child.classList?.contains('background-owner-context-menu'));

    api.setBackgroundOwnerStatusPayloadForTest(followerPayload);
    assert.equal(api.backgroundOwnerOwnsAllRolesForTest(followerPayload), false, 'follower payload is not already leader');
    assert.equal(api.backgroundOwnerCurrentOwnerLiveForTest(followerPayload, ownerServer.last_heartbeat + 1), true, 'fresh owner heartbeat requires confirm');
    const fetchCalls = [];
    const confirmMessages = [];
    api.setFetchForTest((url, options = {}) => {
      fetchCalls.push({url: String(url), method: options.method || 'GET'});
      if (String(url) === '/api/background/claim') return Promise.resolve(jsonResponse({ok: true, claimed: true, was_owner: false, status: leaderPayload}));
      if (String(url) === '/api/background/status') return Promise.resolve(jsonResponse(leaderPayload));
      return Promise.resolve(jsonResponse({ok: true}));
    });
    api.setConfirmForTest(message => {
      confirmMessages.push(String(message));
      return false;
    });

    const cancelEvent = menuEvent();
    api.showBackgroundOwnerContextMenuForTest(cancelEvent);
    const cancelMenu = ownerMenu();
    assert.equal(cancelEvent.defaultPrevented, true, 'right-click suppresses the browser context menu');
    assert.ok(cancelMenu?.firstElementChild?.textContent.includes('Take over as leader'), 'follower menu offers takeover');
    cancelMenu.firstElementChild.listeners.get('click')[0](clickEvent());
    await flushAsyncWork();
    assert.equal(confirmMessages.length, 1, 'live owner takeover asks for confirmation');
    assert.ok(confirmMessages[0].includes('devhost:8002'), 'confirm names the current leader');
    assert.equal(fetchCalls.length, 0, 'canceling the confirm does not claim ownership');

    api.setConfirmForTest(message => {
      confirmMessages.push(String(message));
      return true;
    });
    api.showBackgroundOwnerContextMenuForTest(menuEvent());
    ownerMenu().firstElementChild.listeners.get('click')[0](clickEvent());
    for (let i = 0; i < 4; i += 1) await flushAsyncWork();
    assert.deepStrictEqual(fetchCalls.map(call => `${call.method} ${call.url}`), ['POST /api/background/claim', 'GET /api/background/status'], 'takeover POSTs then refreshes background status');
    assert.ok(api.topbarOwnerStatusHtmlForTest().includes('leader'), 'claim response and refresh flip the topbar state to leader');

    api.setBackgroundOwnerStatusPayloadForTest(leaderPayload);
    api.showBackgroundOwnerContextMenuForTest(menuEvent());
    const leaderMenu = ownerMenu();
    assert.ok(leaderMenu?.firstElementChild?.textContent.includes('Already leader'), 'already-leader menu does not offer takeover');
    assert.equal(leaderMenu.firstElementChild.disabled, true, 'already-leader item is disabled');
  });

  test('t@7156', () => {
    const timing = fs.readFileSync('static_src/js/yolomux/02_timing.js', 'utf8');
    const runtime = fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8');
    const terminal = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
    const layout = fs.readFileSync('static_src/js/yolomux/20_layout_state.js', 'utf8');
    const actions = fs.readFileSync('static_src/js/yolomux/45_file_explorer_actions.js', 'utf8');
    const shareReplay = fs.readFileSync('static_src/js/yolomux/97_share_replay.js', 'utf8');
    assert.ok(/const uiDelayMs = Object\.freeze\(\{[\s\S]*serverWatchRenew:\s*60001[\s\S]*tmuxWindowReadback:\s*120[\s\S]*tmuxWindowReadbackRetry:\s*80[\s\S]*terminalRefreshAfterTabSelect:\s*120[\s\S]*fileQuickOpenDebounce:\s*160[\s\S]*fileExplorerTypeaheadClear:\s*700[\s\S]*shareGeometryDigestPublish:\s*2001/.test(timing), 'RA7/MV-3: remaining frontend timing literals are owned by uiDelayMs and backend-facing cadences are odd');
    assert.ok(runtime.includes("resetRuntimeInterval('server-watch-renew', renewServerWatchRootsFromRuntime, serverWatchRenewMs);"), 'RA7: server watch renewal uses the shared timing owner');
    assert.ok(terminal.includes('const tmuxWindowReadbackDelayMs = tmuxWindowReadbackMs;') && terminal.includes('const tmuxWindowReadbackRetryDelayMs = tmuxWindowReadbackRetryMs;'), 'RA7: tmux readback delays come from the shared timing owner');
    assert.ok(terminal.includes('setTimeout(() => refreshTerminal(session), terminalRefreshAfterTabSelectMs);'), 'RA7: terminal refresh delay uses the shared timing owner');
    assert.ok(layout.includes('fileQuickOpenDebounce = setTimeout(run, fileQuickOpenDebounceMs);'), 'RA7: quick-open debounce uses the shared timing owner');
    assert.ok(actions.includes("setTimeout(() => { fileExplorerTypeaheadBuffer = ''; }, fileExplorerTypeaheadClearMs);"), 'RA7: Finder typeahead clear delay uses the shared timing owner');
    assert.ok(shareReplay.includes('shareGeometryDigestTimer = setInterval(publishShareGeometryDigest, shareGeometryDigestPublishMs);'), 'RA7: share geometry digest loop uses the shared timing owner');
    assert.equal(/server-watch-renew'[\s\S]{0,120}6000[01]/.test(runtime), false, 'RA7: server-watch-renew no longer has an inline minute literal');
    assert.equal(/setTimeout\(run,\s*160\)/.test(layout), false, 'RA7: quick-open debounce no longer has an inline delay');
    assert.equal(/setInterval\(publishShareGeometryDigest,\s*200[01]\)/.test(shareReplay), false, 'RA7: share geometry digest loop no longer has an inline delay');
  });

  test('t@7185-terminal-resize-recovery-and-dispose-guards', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/function scheduleRemoteResize\(session[\s\S]*?!terminalCanPublishRemoteSize\(\)[\s\S]*item\.remoteResizePending = true/.test(source), 'hidden-tab resize skips are marked pending instead of silently disappearing');
    assert.ok(/function forceRemoteResize\(session\)[\s\S]*sendRemoteResize\(session\)/.test(source), 'forced remote resize bypasses unchanged-fit dedupe by sending current terminal dims');
    assert.ok(/function resyncVisibleTerminalRemoteSizes\(reason = ''\)[\s\S]*scheduleFit\(session\)[\s\S]*forceRemoteResize\(session\)/.test(source), 'page-visible and online recovery force-publish current terminal geometry');
    assert.ok(/function requestTerminalScreenRefresh\(session, item = terminals\.get\(session\), reason = 'terminal-refresh'\)[\s\S]*JSON\.stringify\(\{type: 'refresh', reason: refreshReason\}\)/.test(source), 'tmux screen refresh requests carry a reason through the shared websocket refresh message');
    assert.ok(/function refreshVisibleTerminalScreens\(reason = 'manual-refresh'\)[\s\S]*terminalIsVisible\(session, item\.container\)[\s\S]*refreshTerminal\(session\)[\s\S]*requestTerminalScreenRefresh\(session, item, reason\)/.test(source), 'manual refresh repaints visible xterms and asks tmux to redraw those windows');
    assert.ok(/function refreshAll\(\)[\s\S]*resyncVisibleTerminalRemoteSizes\('refresh'\)[\s\S]*refreshVisibleTerminalScreens\('manual-refresh'\)[\s\S]*refreshTranscripts\(\{force: true\}\)/.test(source), 'manual refresh resizes and repaints visible tmux panes before continuing existing refresh work');
    assert.ok(/document\.addEventListener\('visibilitychange'[\s\S]*resyncVisibleTerminalRemoteSizes\('visible'\)/.test(source), 'visibility return resends terminal geometry');
    assert.ok(/window\.addEventListener\('online'[\s\S]*resyncVisibleTerminalRemoteSizes\('online'\)/.test(source), 'network return resends terminal geometry');
    assert.ok(/function closeTerminalItem\(session, item\)[\s\S]*cancelAnimationFrame\(item\.fitFrame\)[\s\S]*clearTimeout\(item\.fitTimer\)[\s\S]*item\.fitFrame = 0[\s\S]*item\.fitTimer = 0/.test(source), 'terminal teardown cancels pending fit callbacks');
    assert.ok(/socket\.onmessage = event => \{[\s\S]*terminals\.get\(session\) !== item[\s\S]*try \{[\s\S]*item\.term\.write/.test(source), 'late websocket frames are ignored after terminal item replacement/dispose');
  });

  // Finder symlink badge — the row toggles is-symlink/symlink-broken, shows a name→target
  // title, and the CSS overlays an arrow badge (red + struck-through for broken).
  test('t@7192', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.equal(source.includes('row.className = `file-tree-row kind-${entry.kind}`'), false, 'Finder row refresh does not drop and re-add symlink/indexed classes');
    assert.ok(source.includes('function buildFileTreeRowState('), 'RA4: row render state has a named builder');
    assert.ok(source.includes('function applyFileTreeRowDataset('), 'RA4: row dataset/class work has a named applier');
    assert.ok(source.includes('function bindFinderRowHandlers('), 'RA4: Finder handlers have a named binder');
    assert.ok(source.includes('function bindDifferRowData('), 'RA4: Differ row data has a named binder');
    assert.ok(/function updateFileTreeRow\([\s\S]*buildFileTreeRowState[\s\S]*applyFileTreeRowDataset[\s\S]*applyFileTreeRowDerivedState[\s\S]*bindDifferRowData[\s\S]*bindFinderRowHandlers/.test(source), 'RA4: updateFileTreeRow is the short dispatcher over the row helpers');
    assert.ok(source.includes('syncFileTreeRowKindClass(row, entry.kind)'), 'Finder row kind classes update through stable toggles');
    assert.ok(source.includes("row.classList.toggle('is-symlink', entry.is_symlink === true)"), 'rows flag symlinks');
    assert.ok(source.includes("row.classList.toggle('symlink-broken', entry.kind === 'symlink-broken')"), 'rows flag broken symlinks');
    assert.ok(/entry\.is_symlink === true && entry\.symlink_target[\s\S]{0,160}→ \$\{entry\.symlink_target\}/.test(source), 'a symlink row title shows name → target');
    // The target renders INLINE in the row name ("name → target"), rel or abs as stored.
    const api = loadYolomux();
    const linkFile = api.fileTreeDisplayParts('/repo/link', {kind: 'file', name: 'link', is_symlink: true, symlink_target: '../real/path.txt'});
    assert.equal(linkFile.text, 'link → ../real/path.txt', 'inline text shows name → target');
    assert.ok(linkFile.html.includes('file-tree-symlink-target') && linkFile.html.includes('→ ../real/path.txt'), 'inline target is its own dimmed span');
    const linkDir = api.fileTreeDisplayParts('/repo/ld', {kind: 'dir', name: 'ld', is_symlink: true, symlink_target: '/abs/target'});
    assert.ok(linkDir.text.includes('ld → /abs/target'), 'a symlinked dir shows its absolute target inline');
    const plain = api.fileTreeDisplayParts('/repo/f.txt', {kind: 'file', name: 'f.txt'});
    assert.ok(!plain.text.includes('→'), 'a non-symlink has no target suffix');
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/\.file-tree-row\.is-symlink > \.file-tree-icon::after\s*\{[^}]*content:\s*"↪"/.test(css), 'the symlink icon gets an arrow-badge overlay');
    assert.ok(/\.file-tree-row\.symlink-broken[^{]*\.file-tree-icon::after\s*\{[^}]*color:\s*var\(--bad\)/.test(css), 'a broken symlink badge is red (token)');
    assert.ok(/\.file-tree-row\.symlink-broken[^{]*\.file-tree-name\s*\{[^}]*line-through/.test(css), 'a broken symlink name is struck through');
  });

  test('t@7214', () => {
    const api = loadYolomux();
    const strip = tabStrip([
      tabElement('1', 100, 100),
      tabElement('2', 203, 100),
      tabElement('3', 306, 100),
    ]);

    assert.deepStrictEqual(canonical(api.paneTabDropPlacement(strip, {clientX: 110, clientY: 8}, '9')), {index: 0, x: 2, y: 0, height: 27, noop: false});
    assert.deepStrictEqual(canonical(api.paneTabDropPlacement(strip, {clientX: 225, clientY: 8}, '9')), {index: 1, x: 103, y: 0, height: 27, noop: false});
    assert.deepStrictEqual(canonical(api.paneTabDropPlacement(strip, {clientX: 390, clientY: 8}, '9')), {index: 3, x: 304, y: 0, height: 27, noop: false});
    assert.deepStrictEqual(canonical(api.paneTabDropPlacement(strip, {clientX: 225, clientY: 8}, '2')), {index: 1, x: 206, y: 0, height: 27, noop: true});
    assert.deepStrictEqual(canonical(api.paneTabDropPlacement(tabStrip([]), {clientX: 180, clientY: 8}, '9')), {index: 0, x: 80, y: 0, height: 28, noop: false});
    assert.equal(api.paneTabDropIndex(strip, {clientX: 225, clientY: 8}, '9'), 1);

    const multiLineStrip = tabStrip([
      tabElement('1', 100, 100, 0),
      tabElement('2', 203, 100, 0),
      tabElement('3', 100, 100, 30),
      tabElement('4', 203, 100, 30),
    ]);
    multiLineStrip.rect = {left: 100, right: 406, top: 0, bottom: 58, width: 306, height: 58};
    assert.deepStrictEqual(canonical(api.paneTabDropPlacement(multiLineStrip, {clientX: 110, clientY: 38}, '9')), {index: 2, x: 2, y: 30, height: 27, noop: false});
    assert.deepStrictEqual(canonical(api.paneTabDropPlacement(multiLineStrip, {clientX: 225, clientY: 38}, '9')), {index: 3, x: 103, y: 30, height: 27, noop: false});
  });

  test('t@7240', () => {
    // View -> Theme is a submenu of discrete System/Dark/Light one-click items.
    const api = loadYolomux('', ['1']);
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    const themeSubmenu = api.appMenuTree()
      .flatMap(menu => Array.isArray(menu.items) ? menu.items : [])
      .find(item => item.type === 'submenu' && item.label === 'Theme');
    assert.ok(themeSubmenu, '#24: View has a Theme submenu');
    const themeLabels = themeSubmenu.items.filter(item => item.type === 'command').map(item => item.label);
    assert.deepStrictEqual([...themeLabels], ['System', 'Dark', 'Light'], '#24: Theme submenu offers System/Dark/Light as discrete one-click items');
    assert.ok(themeSubmenu.items.some(item => item.label === 'Dark' && item.checked !== undefined), '#24: Theme items carry a checked state for the current mode');
    // The active marker tracks the LIVE theme (regression: normalizeGlobalThemeMode() with no arg used to
    // always return 'dark', so Dark stayed marked even after switching to Light).
    const themeCheckedFor = mode => {
      api.setGlobalThemeModeForTest(mode);
      return api.appMenuTree()
        .flatMap(menu => Array.isArray(menu.items) ? menu.items : [])
        .find(item => item.type === 'submenu' && item.label === 'Theme')
        .items.filter(item => item.checked === true).map(item => item.label);
    };
    assert.deepStrictEqual([...themeCheckedFor('light')], ['Light'], 'theme marker: only Light is checked when light is the live mode');
    assert.deepStrictEqual([...themeCheckedFor('dark')], ['Dark'], 'theme marker: only Dark is checked when dark is the live mode');
    assert.deepStrictEqual([...themeCheckedFor('system')], ['System'], 'theme marker: only System is checked when system is the live mode');
    // setGlobalThemeMode rebuilds the menu bar so the marker updates immediately (not on the next poll).
    assert.ok(/function setGlobalThemeMode[\s\S]*?applyGlobalThemeMode\([^)]*\);\s*renderSessionButtons\(\)/.test(source), 'setGlobalThemeMode re-renders the menu bar so the active marker updates at once');
    // #258: picking a theme APPLIES it live (the menu used to only save the patch).
    // #258: the theme applies live (body.theme-* flips), now via the shared apply+save helper that both
    // the one-click Theme submenu and the View cycle delegate to.
    assert.ok(/function applyAndSaveGlobalTheme[\s\S]*?globalThemeMode = next;\s*applyGlobalThemeMode\(\{updateEditor: true, updateTerminals: true\}\)/.test(source), '#258: applyAndSaveGlobalTheme applies the theme live');
    assert.ok(/function setGlobalThemeMode\(mode\)\s*\{\s*return applyAndSaveGlobalTheme\(normalizeGlobalThemeMode\(mode\)\)/.test(source), '#258: setGlobalThemeMode delegates to the shared apply+save helper');
    assert.ok(/function cycleGlobalThemeSetting\(\)\s*\{\s*return applyAndSaveGlobalTheme\(nextGlobalThemeMode\(\)\)/.test(source), '#258: cycleGlobalThemeSetting delegates to the shared apply+save helper');
    // #261: the View menu no longer PINS the terminal palette — it just follows the app (follow-app stays).
    assert.equal(/function setGlobalThemeMode[\s\S]*?patch\.appearance\.terminal_theme/.test(source), false, '#261: setGlobalThemeMode no longer pins appearance.terminal_theme');
    assert.equal(/function cycleGlobalThemeSetting[\s\S]*?patch\.appearance\.terminal_theme/.test(source), false, '#261: cycleGlobalThemeSetting no longer pins appearance.terminal_theme');
    // Active-terminal cursor: the focused pane's terminal shows the configured cursor color.
    assert.ok(/const DEFAULT_CURSOR_COLOR\s*=\s*'yellow'/.test(source), 'active-terminal cursor: yellow remains the default cursor color');
    assert.ok(/const UI_COLOR_PRESETS\s*=\s*\{[\s\S]*yellow:\s*\{labelKey:\s*'pref\.appearance\.active_color\.yellow',\s*cursorLabelKey:\s*'pref\.appearance\.editor_cursor_color\.yellow',\s*cursor:\s*\{dark:\s*'#ffea00',\s*light:\s*'#9a6700'\}/.test(source), 'active-terminal cursor: bright yellow cursor color lives in the shared UI color parent with a light-mode variant');
    assert.ok(/function terminalThemeForSession[\s\S]*?badConnectionCursorStateActive\(\)[\s\S]*?terminalThemeWithBadConnectionCursor\(theme\)[\s\S]*?session === focusedPanelItem \? \{\.\.\.theme, cursor: activeTerminalCursorColorForTheme\(theme\)\}/.test(source), 'active-terminal cursor: the focused session gets the configured cursor color unless bad connection owns the cursor');
    assert.ok(/item\.term\.options\.cursorBlink = terminalCursorBlinkEnabled\(\)[\s\S]*?item\.term\.options\.theme = terminalThemeForSession\(session, theme\)/.test(source), 'active-terminal cursor: applyTerminalRuntimeSettings updates blink state and themes the active terminal with the configured cursor color');
    assert.ok(/cursorBlink: typeof terminalCursorBlinkEnabled === 'function' \? terminalCursorBlinkEnabled\(\) : true/.test(source) && /theme: terminalThemeForSession\(session, baseTheme\)/.test(source), 'active-terminal cursor: a newly-created terminal uses shared blink/theme cursor helpers');
    assert.ok(/function refreshActiveTerminalCursor[\s\S]*?item\.term\.options\.cursorBlink = !badConnection/.test(source), 'active-terminal cursor: connection changes can disable cursor blinking without a full terminal rebuild');
    assert.ok(/function updatePanelInactiveOverlays[\s\S]*?refreshActiveTerminalCursor\(\)/.test(source), 'active-terminal cursor: focus changes refresh the cursor color (refreshActiveTerminalCursor)');
  });

  test('t@7283', () => {
    // YO!agent composer redesign (mockup 044): a rounded input bar with the input on top and a control
    // row below — backend/model/effort selectors (wired to YO!agent settings) + subtle Clear + a circular send arrow.
    const src = fs.readFileSync('static/yolomux.js', 'utf8');
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/class="yoagent-chat-controls"/.test(src), 'YO!agent composer has a control row');
    assert.ok(/function yoagentComposerControlsHtml/.test(src) && /kind: 'backend'/.test(src) && /kind: 'model'/.test(src) && /kind: 'effort'/.test(src), 'composer renders backend/model/effort selectors');
    assert.ok(/data-yoagent-setting-path/.test(src) && /saveSettingsPatch\(settingPatchForPath\(path, yoagentSetting\.value\)/.test(src), 'changing a composer selector writes the real YO!agent setting path through the shared patch helper');
    assert.ok(/class="yoagent-chat-send-icon"[\s\S]*?<path/.test(src), 'send button is a circular arrow icon (not a text "Ask" button)');
    assert.ok(/\.yoagent-chat-form\s*\{[^}]*border-radius:\s*14px/.test(css), 'composer is one rounded container');
    assert.ok(/\.yoagent-chat-form\s*\{[\s\S]*border:\s*1px solid var\(--link-soft\)/.test(css), 'YO!agent composer border reuses the YOU bubble color token');
    assert.ok(/\.yoagent-chat-send\s*\{[^}]*border-radius:\s*50%/.test(css), 'send button is circular');
    assert.ok(/\.yoagent-composer-pill,\s*\n\.yoagent-backend-pill\s*\{/.test(css), 'composer selectors are styled as compact pills');
    assert.ok(/\.yoagent-backend-pill-dot\s*\{[\s\S]*background:\s*var\(--agent-inactive-marker-bg\)[\s\S]*border:\s*2px solid var\(--yoagent-inactive-backend-dot-border\)[\s\S]*box-shadow:/.test(css), 'YO!agent inactive backend dot is a clear black circle with a token-owned visible border');
    assert.ok(/\.yoagent-composer-pill-backend:not\(:has\(select:disabled\)\) \.yoagent-backend-pill-dot\s*\{[\s\S]*background:\s*var\(--pr-status-passing\)[\s\S]*box-shadow:/.test(css), 'YO!agent usable backend dot switches to the green active status without reusing the current-window marker');
    assert.ok(/body\.theme-light \.yoagent-backend-pill-dot\s*\{[\s\S]*background:\s*var\(--agent-inactive-marker-bg\)[\s\S]*border-color:\s*var\(--agent-inactive-marker-border\)/.test(css), 'YO!agent inactive backend dot keeps its black fill with an explicit light-mode border pair');
    assert.ok(/\.yoagent-recent-agents-list\s*\{[^}]*display:\s*grid/.test(css), 'YO!agent recent agents render as a compact bullet list inside the chat history');
    assert.ok(/function yoagentRecentAgentPathText\(agent, signal = yoagentRecentAgentSignal\(agent\)\)[\s\S]*agent\?\.recent_paths[\s\S]*compactHomePath/.test(src), 'YO!agent recent agents display backend recent_paths with compact home paths');
    assert.ok(/function yoagentRecentAgentsHtml\(payload = yoagentStartupActivityPayload\(\)\)[\s\S]*payload\?\.agents[\s\S]*<ul class="yoagent-recent-agents-list">/.test(src), 'YO!agent recent agents render from the startup activity-summary snapshot as a list');
    assert.ok(/function yoagentRecentAgentsMessageHtml\(\)[\s\S]*yoagentRecentAgentsHtml\(payload\)[\s\S]*yoagent-message assistant yoagent-recent-agents-message/.test(src), 'YO!agent recent agents are wrapped as an assistant response for the startup one-shot');
    assert.ok(/let yoagentStreamingMessages = new Map\(\)/.test(src), 'YO!agent keeps transient streaming assistant messages in chat state');
    assert.ok(/function yoagentStreamingMessagesList\(\)[\s\S]*yoagentStreamingMessages\.values/.test(src), 'YO!agent exposes streamed assistant deltas as renderable messages');
    assert.ok(/function applyYoagentStreamPayload\(payload = \{\}\)[\s\S]*hidden_thinking_removed[\s\S]*raw model thinking was hidden/.test(src), 'YO!agent stream events expose safe thinking diagnostics without raw chain-of-thought');
    assert.ok(/function applyYoagentStreamPayload\(payload = \{\}\)[\s\S]*auxiliary_lines[\s\S]*auxiliaryPreview[\s\S]*auxiliaryText[\s\S]*auxiliaryTruncated/.test(src), 'YO!agent stream payloads keep auxiliary thinking/tool lines separate from assistant content');
    assert.ok(src.includes("'yoagent_stream_delta'"), 'YO!agent subscribes to streaming SSE events');
    assert.ok(/function yoagentChatMessagesHtml\(\)[\s\S]*const startupInfo = yoagentStartupInfoVisible \? yoagentStartupInfoHtml\(\) : '';[\s\S]*return `\$\{messageHtml\}\$\{startupInfo\}`;/.test(src), 'YO!agent startup info is state-gated instead of always appended after messages');
    assert.ok(/function showYoagentStartupInfoOnce\(\)[\s\S]*captureYoagentStartupActivitySummarySnapshot\(\)[\s\S]*yoagentStartupInfoVisible = true/.test(src), 'YO!agent startup info freezes the activity snapshot when it is printed');
    assert.ok(/function showYoagentStartupInfoForLatestActivity\(\)[\s\S]*resetYoagentStartupActivitySummarySnapshot\(\)[\s\S]*yoagentStartupInfoShown = false[\s\S]*showYoagentStartupInfoOnce\(\)/.test(src), 'YO!agent can intentionally re-show the latest activity snapshot after clearing conversation');
    assert.ok(/function applyActivitySummaryPayloadFromPush\(payload = \{\}, options = \{\}\)[\s\S]*options\.refreshStartupSnapshot === true[\s\S]*captureYoagentStartupActivitySummarySnapshot\(\{replace: true\}\)/.test(src), 'activity-summary pushes are cache-only unless an explicit refresh requests a new startup snapshot');
    assert.ok(/async function prewarmYoagent\(options = \{\}\)[\s\S]*visible: shouldRequestStartupAnswer[\s\S]*applyYoagentConversationPayload\(payload\.conversation/.test(src), 'YO!agent prewarm asks for one visible startup LLM answer and applies the saved conversation');
    assert.ok(/async function clearYoagentConversation\(\)[\s\S]*yoagentPrewarmStarted = false[\s\S]*showYoagentStartupInfoForLatestActivity\(\)[\s\S]*refreshActivitySummary\(\{force: true, silent: true\}\)[\s\S]*showYoagentStartupInfoForLatestActivity\(\)/.test(src), 'Clear conversation resets prewarm and re-renders the refreshed latest activity snapshot');
    assert.equal(/function applyYoagentConversationPayload\(payload = \{\}\)[\s\S]*if \(messages\.length\) hideYoagentStartupInfo\(\)/.test(src), false, 'YO!agent real conversation payloads keep the startup Recent agents block');
    assert.ok(/function applyYoagentConversationPayload\(payload = \{\}\)[\s\S]*hasOwnProperty\.call\(payload, 'messages'\)[\s\S]*return false/.test(src), 'YO!agent ignores partial/missing conversation payloads instead of clearing visible history');
    assert.ok(/let yoagentPendingWaits = \[\]/.test(src), 'YO!agent keeps server-reported pending waits in chat state');
    assert.ok(/function applyYoagentConversationPayload\(payload = \{\}\)[\s\S]*yoagentPendingWaits = Array\.isArray\(payload\.pending_waits\)/.test(src), 'YO!agent conversation payload carries pending background waits');
    assert.ok(/function yoagentPendingWaitsHtml\(\)[\s\S]*tPlural\('yoagent\.waiting\.count'[\s\S]*yoagent-waiting-queue/.test(src), 'YO!agent renders a waiting queue for one or more background result waits');
    assert.ok(/function yoagentPendingWaitsHtml\(\)[\s\S]*sourceRegarding[\s\S]*targetRegarding[\s\S]*yoagent\.waiting\.handoff[\s\S]*yoagent\.waiting\.session/.test(src), 'YO!agent waiting rows distinguish handoff waits from direct session waits and include both regarding summaries');
    assert.ok(/data-yoagent-wait-clear/.test(src) && /async function clearYoagentPendingWait/.test(src), 'YO!agent pending waits expose a clear affordance through the existing wait store');
    assert.ok(/function applyYoagentJobsPayload\(payload = \{\}\)[\s\S]*yoagentJobs = Array\.isArray\(payload\.jobs\)/.test(src), 'YO!agent keeps server-reported jobs in chat state');
    assert.ok(/function yoagentJobsHtml\(\)[\s\S]*yoagent-jobs-list/.test(src), 'YO!agent renders queued jobs as a visible list');
    assert.ok(/data-yoagent-job-confirm/.test(src) && /data-yoagent-job-cancel/.test(src), 'YO!agent job rows expose confirm/cancel controls');
    assert.ok(/async function loadYoagentJobs\(options = \{\}\)[\s\S]*apiFetchJson\('\/api\/yoagent\/jobs'/.test(src), 'YO!agent hydrates jobs from the existing jobs API');
    assert.ok(/type === 'yoagent_jobs_changed'[\s\S]*loadYoagentJobs\(\{force: true, silent: true, render: yoagentPanelIsActive\(\)/.test(src), 'YO!agent job SSE refreshes the visible job list');
    assert.ok(/\[data-yoagent-job-confirm\][\s\S]*confirmYoagentJob/.test(src) && /\[data-yoagent-job-cancel\][\s\S]*cancelYoagentJob/.test(src), 'YO!agent job controls are delegated from the YO!agent panel');
    assert.ok(/function yoagentShouldScrollBottom\(options, scrollState\)[\s\S]*options\.scrollBottom === true[\s\S]*options\.scrollBottom === false[\s\S]*yoagentScrollbackLocked[\s\S]*scrollState\?\.nearBottom/.test(src), 'YO!agent chat auto-scrolls only when forced or already near the bottom and not manually scrollback-locked');
    assert.ok(/function yoagentChatScrollOwner\(node = document\.getElementById\('yoagent-content'\)\)\s*\{[\s\S]*return node\?\.querySelector\?\.\('\.yoagent-chat-history'\) \|\| node \|\| null;[\s\S]*function scrollYoagentChatToBottom/.test(src), 'YO!agent has one normal scroll owner with only the outer node as a fallback');
    assert.ok(/function scrollYoagentChatToBottom\(node = document\.getElementById\('yoagent-content'\)\)[\s\S]*const owner = yoagentChatScrollOwner\(node\);[\s\S]*owner\.scrollTop = owner\.scrollHeight[\s\S]*yoagentScrollbackLocked = false/.test(src), 'YO!agent bottom-scroll drives only the chosen scroll owner');
    assert.ok(/function yoagentChatScrollState\(node = document\.getElementById\('yoagent-content'\)\)[\s\S]*const owner = yoagentChatScrollOwner\(node\);[\s\S]*ownerTop: owner \? owner\.scrollTop : 0/.test(src), 'YO!agent scroll state stores only the chosen owner top');
    assert.equal(/yoagentChatScrollState[\s\S]{0,420}(nodeTop|panelTop|panelBody)/.test(src), false, 'YO!agent scroll state does not capture outer list or panel body scroll positions');
    assert.ok(/function restoreYoagentChatScrollState\(node, state\)[\s\S]*const owner = yoagentChatScrollOwner\(node\);[\s\S]*owner\.scrollTop = state\.ownerTop \|\| 0[\s\S]*yoagentScrollbackLocked = state\.nearBottom === false/.test(src), 'YO!agent restores only the chosen scroll owner and preserves the scrollback lock');
    assert.ok(/function installYoagentChatScrollTracker\(node = document\.getElementById\('yoagent-content'\)\)[\s\S]*const history = yoagentChatScrollOwner\(node\);[\s\S]*addEventListener\('scroll'[\s\S]*yoagentScrollbackLocked = !yoagentChatHistoryIsNearBottom\(history\)/.test(src), 'YO!agent chat records manual scrollback on the single scroll owner');
    assert.ok(/data-yoagent-chat-form[\s\S]*addEventListener\('wheel'[\s\S]*history\.scrollTop = nextTop/.test(src), 'YO!agent composer wheel events forward to the single history scroll owner');
    assert.ok(src.includes("loadYoagentConversation({force: true, render: yoagentPanelIsActive(), scrollBottom: 'auto'})"), 'YO!agent background result pushes preserve manual scrollback unless the chat is already near bottom');
    assert.ok(/\.yoagent-transcript-path\s*\{[^}]*display:\s*flex[\s\S]*min-width:\s*0/.test(css), 'YO!agent transcript path row is compact and ellipsizes inside the chat panel');
    assert.ok(/\.yoagent-transcript-value\s*\{[^}]*text-overflow:\s*ellipsis/.test(css), 'YO!agent transcript path cannot overflow the chat panel');
    assert.ok(/\.yoagent-message\.assistant\s*\{[\s\S]*align-self:\s*flex-start[\s\S]*margin-inline-end:\s*28px[\s\S]*border-color:\s*var\(--active-control-border\)[\s\S]*background:\s*color-mix\(in srgb, var\(--active-control-soft-bg\)/.test(css), 'YO!agent assistant bubbles are left-indented and use the active theme accent');
    assert.ok(/\.yoagent-message\.assistant\.yoagent-agent-result\s*\{[\s\S]*border-inline-start-color:\s*var\(--accent-gold\)[\s\S]*border-inline-start-width:\s*6px/.test(css), 'YO!agent target-agent result bubbles have a stronger colored left rule');
    assert.ok(/function yoagentAgentResultParts\(text\)[\s\S]*heading[\s\S]*output/.test(src), 'YO!agent target-agent result parser splits the heading from the output');
    assert.ok(/\.yoagent-agent-result-body\s*\{[\s\S]*display:\s*grid[\s\S]*gap:\s*0/.test(css), 'YO!agent target-agent result body stacks heading and output without extra vertical gap');
    assert.ok(/\.yoagent-message\.assistant\.yoagent-agent-result \.yoagent-agent-result-output\s*\{[\s\S]*padding-inline-start:\s*14px[\s\S]*border-inline-start:\s*3px solid var\(--accent-gold\)/.test(css), 'YO!agent target-agent result output is indented behind a full-height left bar');
    assert.ok(/\.yoagent-message\.user\s*\{[\s\S]*align-self:\s*flex-end[\s\S]*margin-inline-start:\s*28px[\s\S]*border-color:\s*var\(--link-soft\)/.test(css), 'YO!agent user bubbles are right-indented with the secondary/link border color');
    assert.ok(/\.yoagent-message\s*\{[\s\S]*overflow:\s*visible[\s\S]*overscroll-behavior:\s*auto/.test(css), 'YO!agent message bubbles are not vertical scroll containers that swallow wheel input');
    assert.ok(/\.yoagent-message-body\s*\{[\s\S]*overflow-x:\s*visible[\s\S]*overflow-y:\s*visible[\s\S]*overscroll-behavior:\s*auto/.test(css), 'YO!agent message bodies leave vertical wheel scrolling to the chat history owner');
    assert.ok(/function yoagentTimestampText[\s\S]*second:\s*'2-digit'/.test(src), 'YO!agent chat timestamps include seconds');
    assert.ok(/function yoagentMessageLatencyHtml[\s\S]*yoagent-message-latency[\s\S]*yoagent\.responseLatency/.test(src), 'YO!agent assistant timestamps include a localized response-latency suffix');
    assert.ok(/function yoagentMessageDetailsHtml[\s\S]*data-yoagent-message-details-key/.test(src), 'YO!agent assistant diagnostics render as an expandable details block with a stable message key');
    assert.ok(/function yoagentAuxiliaryLineIsDiagnostic[\s\S]*usage:[\s\S]*response time/.test(src), 'YO!agent collapsed details filter diagnostics out of auxiliary previews');
    assert.ok(/function yoagentMessageDetailsHtml[\s\S]*yoagentThinkingDetailsPreview[\s\S]*yoagentDetailsPreviewHtml[\s\S]*yoagent-auxiliary-stream[\s\S]*yoagent-details-note/.test(src), 'YO!agent assistant diagnostics render active thinking preview, expanded stream, and truncation note');
    assert.ok(/function yoagentToolLineHtml[\s\S]*yoagent-tc-command/.test(src), 'YO!agent tool-call lines wrap executed commands in a dedicated command span');
    assert.ok(/t\('yoagent\.toolCall\.label'\)/.test(src), 'YO!agent tool-call details use the localized "tool call" label instead of TC');
    assert.ok(/\.yoagent-tc-command\s*\{[\s\S]*color:\s*var\(--code-function\)/.test(css), 'YO!agent tool-call command span has a distinct themed color');
    assert.ok(/function refreshYoagentSummaryRegions[\s\S]*const openDetails = yoagentOpenMessageDetailsState\(node\)[\s\S]*restoreYoagentOpenMessageDetailsState\(node, openDetails\)/.test(src), 'YO!agent summary refresh preserves expanded Details blocks');
    assert.ok(/function renderYoagentPanel[\s\S]*const openDetails = yoagentOpenMessageDetailsState\(node\)[\s\S]*node\.innerHTML = yoagentChatHtml\(\);[\s\S]*restoreYoagentOpenMessageDetailsState\(node, openDetails\)/.test(src), 'YO!agent full chat rerenders preserve expanded Details blocks');
    assert.ok(/\.yoagent-message-details pre\s*\{[\s\S]*max-height:\s*180px/.test(css), 'YO!agent diagnostics details stay bounded inside the message');
    assert.ok(/\.yoagent-message-details pre\s*\{[\s\S]*overscroll-behavior:\s*auto/.test(css), 'YO!agent diagnostics details allow vertical wheel chaining at scroll edges');
    assert.ok(/\.yoagent-message-details summary::before\s*\{[\s\S]*content:\s*"›"/.test(css), 'YO!agent diagnostics summary renders the shared disclosure chevron glyph');
    assert.ok(/\.yoagent-message-details\[open\] summary::before\s*\{[\s\S]*content:\s*"›"/.test(css), 'YO!agent diagnostics summary keeps the same rotated disclosure chevron glyph when expanded');
    assert.equal(/\.yoagent-message-details summary::before\s*\{[\s\S]*border-inline-start:\s*6px solid currentColor/.test(css), false, 'YO!agent diagnostics no longer use a bespoke CSS-border triangle');
    assert.ok(/\.yoagent-details-preview\s*\{[\s\S]*max-height:\s*calc\(2 \* max/.test(css), 'YO!agent default collapsed auxiliary preview reserves at most two lines');
    assert.ok(/\.yoagent-details-preview\.yoagent-thinking-live-preview\s*\{[\s\S]*max-height:\s*calc\(5 \* max/.test(css), 'YO!agent live thinking collapsed preview reserves five visual lines while running');
    assert.ok(/\.yoagent-details-preview\s*\{[\s\S]*overflow:\s*clip/.test(css), 'YO!agent collapsed auxiliary preview clips without becoming a hidden scroll container');
    assert.ok(/\.yoagent-message-details pre\.yoagent-auxiliary-stream\s*\{[\s\S]*color:\s*color-mix/.test(css), 'YO!agent auxiliary stream is visually quieter than normal chat text');
    assert.ok(/body\.theme-light \.yoagent-message,[\s\S]*body\.theme-light \.yoagent-message-body,[\s\S]*color:\s*var\(--lt-text\)/.test(css), 'YO!agent light-mode message bodies use readable light-mode text');
    assert.ok(/body\.theme-light \.yoagent-chat-input\s*\{[\s\S]*color:\s*var\(--lt-text\)/.test(css), 'YO!agent light-mode composer input uses readable light-mode text');
    assert.ok(/\.yoagent-chat-history\s*\{[\s\S]*overflow-x:\s*hidden[\s\S]*overflow-y:\s*auto[\s\S]*scrollbar-gutter:\s*stable/.test(css), 'YO!agent chat history is the single normal vertical scrollbar with a stable gutter');
    assert.ok(/\.yoagent-chat-history\s*\{[\s\S]*--pane-scrollbar-current-thumb:\s*var\(--pane-scrollbar-thumb\)[\s\S]*--pane-scrollbar-current-track:\s*var\(--pane-scrollbar-track\)/.test(css), 'YO!agent history keeps the normal rail neutral during active-pane hover');
    assert.ok(/\.yoagent-chat-history::\-webkit-scrollbar-thumb:hover,[\s\S]*\.yoagent-chat-history::\-webkit-scrollbar-thumb:active\s*\{[\s\S]*background:\s*var\(--pane-scrollbar-thumb-active\)/.test(css), 'YO!agent history uses the bright thumb only for direct scrollbar hover or drag');
    assert.ok(/\.yoagent-waiting-queue\s*\{[\s\S]*border:\s*1px solid var\(--active-control-soft-border\)/.test(css), 'YO!agent pending waits render as a visible compact queue');
    assert.ok(/\.yoagent-jobs-list\s*\{[\s\S]*border:\s*1px solid var\(--line\)/.test(css), 'YO!agent jobs render as a visible compact queue');
    assert.ok(/\.yoagent-job-controls\s*\{[\s\S]*display:\s*flex/.test(css), 'YO!agent job controls are visible inline controls');
    const actionCardStart = src.indexOf('function yoagentActionCardHtml(action)');
    const actionCardEnd = src.indexOf('function yoagentIntroMessageText', actionCardStart);
    const actionCardBody = src.slice(actionCardStart, actionCardEnd);
    assert.ok(actionCardStart >= 0 && actionCardBody.includes('data-yoagent-action-card') && actionCardBody.includes('data-yoagent-action-send'), 'YO!agent action previews render as confirmed-send cards');
    assert.ok(actionCardBody.includes("t('yoagent.action.preview')") && actionCardBody.includes("t('yoagent.action.send')"), 'YO!agent action card labels are localized');
    assert.ok(src.includes("t('yoagent.statusActionSent'") && src.includes("t('yoagent.statusBackend'"), 'YO!agent action/backend status strings are localized');
    assert.ok(/\.yoagent-chat \.markdown-body pre[\s\S]*?border-radius:\s*8px/.test(css), 'YO!agent code blocks are soft rounded boxes');
    assert.ok(/\.yoagent-chat \.markdown-body pre,[\s\S]*\.yoagent-global \.markdown-body pre\s*\{[\s\S]*overflow-x:\s*auto[\s\S]*overflow-y:\s*auto[\s\S]*overscroll-behavior:\s*auto/.test(css), 'YO!agent code blocks keep horizontal scrolling and normal vertical wheel chaining');
    assert.ok(/body\.theme-light \.yoagent-chat \.markdown-body pre/.test(css), 'YO!agent code blocks get a light box + dark text in light mode');
    assert.ok(/--lt-code-block-bg:\s*#f3f4f6;[\s\S]*--lt-code-block-border:\s*#e4e7ec;[\s\S]*--lt-code-block-text:\s*#1f2328;/.test(css), 'R4: neutral light code-block values live in the shared lt token owner');
    assert.ok(/body\.theme-light \.yoagent-chat,[\s\S]*body\.theme-light \.yoagent-message\s*\{[\s\S]*background:\s*var\(--panel\);[\s\S]*border-color:\s*var\(--line\);/.test(css), 'R4: YO!agent light bubbles use shared panel and line tokens');
    assert.ok(/body\.theme-light \.yoagent-message-details pre\s*\{[\s\S]*background:\s*var\(--lt-code-block-bg\);[\s\S]*border-color:\s*var\(--lt-code-block-border\);/.test(css), 'R4: YO!agent details code blocks use shared neutral code-block tokens');
    assert.ok(/body\.theme-light \.yoagent-chat \.markdown-body pre,[\s\S]*body\.theme-light \.yoagent-global \.markdown-body pre\s*\{[\s\S]*background:\s*var\(--lt-code-block-bg\);[\s\S]*border-color:\s*var\(--lt-code-block-border\);[\s\S]*color:\s*var\(--lt-code-block-text\);/.test(css), 'R4: YO!agent markdown code blocks use shared neutral code-block tokens');
    assert.ok(/\.file-editor-theme-panel\.theme-vanilla\s*\{[\s\S]*background:\s*var\(--lt-editor-bg\);[\s\S]*border-color:\s*var\(--lt-line\);/.test(css), 'R4: editor vanilla swatch uses light editor tokens');
    assert.ok(/body\.theme-light \.command-palette-dialog,[\s\S]*body\.theme-light \.keyboard-shortcuts-dialog\s*\{[\s\S]*background:\s*var\(--panel\);[\s\S]*border-color:\s*var\(--line\);/.test(css), 'R4: command palette and shortcuts dialogs use shared light panel tokens');
    assert.ok(/body\.theme-light \.yoagent-message-body\.markdown-body,[\s\S]*?\.yoagent-global \.markdown-body\s*\{[^}]*color:\s*var\(--lt-text\)/.test(css), 'YO!agent light-mode markdown bodies use dark app text instead of editor markdown colors');
    assert.ok(/body\.theme-light \.yoagent-chat \.markdown-body strong,[\s\S]*?\.yoagent-global \.markdown-body strong\s*\{[^}]*color:\s*var\(--lt-text\)/.test(css), 'YO!agent light-mode bold text is readable, not white-on-light');
    assert.ok(/body\.theme-light \.yoagent-chat \.markdown-body :not\(pre\) > code,[\s\S]*?\.yoagent-global \.markdown-body :not\(pre\) > code\s*\{[^}]*color:\s*#0f4c81/.test(css), 'YO!agent light-mode inline code uses a readable app-blue chip');
    // Rendered-markdown chat bodies drop pre-wrap so bullet lists are tightly spaced (the preserved
    // newlines between/inside the generated <ul><li> HTML were widening them).
    assert.ok(/\.yoagent-message-body\.markdown-body\s*\{[^}]*white-space:\s*normal/.test(css), 'rendered markdown chat bodies use white-space:normal so bullets are not widely spaced');
    // The "thinking" busy indicator uses the shared real-span moving ellipsis. Do not fork a second
    // pseudo-element or per-feature keyframe animation.
    assert.ok(/function movingEllipsisHtml\(className = ''\)[\s\S]*<span>\.<\/span><span>\.<\/span><span>\.<\/span>/.test(src), 'moving dots render as three real animated spans from one helper');
    assert.ok(src.includes("textWithMovingEllipsisHtml(t('yoagent.thinking'), 'yoagent-thinking-dots')"), 'YO!agent thinking uses the shared moving ellipsis helper');
    assert.ok(/\.moving-ellipsis span\s*\{[^}]*animation:\s*moving-ellipsis-dot/.test(css), 'moving dot spans animate directly');
    assert.ok(/\.moving-ellipsis span\s*\{[^}]*opacity:\s*0/.test(css), 'moving dots start hidden so the ellipsis visibly cycles');
    assert.ok(/\.moving-ellipsis span:nth-child\(2\)\s*\{[^}]*animation-delay:\s*0\.2s/.test(css), 'moving dot 2 is staggered');
    assert.ok(/\.moving-ellipsis span:nth-child\(3\)\s*\{[^}]*animation-delay:\s*0\.4s/.test(css), 'moving dot 3 is staggered');
    assert.equal((css.match(/@keyframes moving-ellipsis-dot/g) || []).length, 1, 'the moving-dot keyframes have one shared owner');
    assert.equal(/@keyframes (yoagent-thinking-dot|tabber-loading-dots)/.test(css), false, 'old per-feature moving-dot keyframes stay removed');
    assert.equal(/prefers-reduced-motion[^{]*\{[^}]*yoagent-thinking-dots/.test(css), false, 'thinking dots keep blinking even when reduced-motion CSS is active');
    // #YO!info scroll: the body pane (a grid item of the .panel grid) must keep min-width:0 so wide
    // content scrolls inside .info-list (overflow:auto) instead of blowing the column out past the
    // overflow:hidden panel (which silently clipped the right side — the user could not scroll right).
    assert.ok(/\.info-pane\s*\{[^}]*min-width:\s*0/.test(css), 'YO!info body pane keeps min-width:0 so wide content scrolls instead of being clipped');
    assert.ok(/\.info-list\s*\{[^}]*overflow:\s*auto/.test(css), 'YO!info list owns the scroll (overflow:auto, both axes)');
    const en = JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'));
    assert.equal(en['yoagent.chatPlaceholder'], 'Ask anything…', 'composer placeholder matches the mockup ("Ask anything…")');
    assert.equal(en['yoagent.jobs.title'], 'YO!agent jobs', 'YO!agent job-list title is localized');
    assert.equal(en['yoagent.jobs.confirm'], 'Confirm', 'YO!agent job confirm button is localized');
    assert.equal(en['yoagent.waiting.count.other'], 'Waiting for {count} replies', 'YO!agent pending-wait count is localized');
    assert.equal(en['yoagent.waiting.handoff'], 'Waiting for tmux session `{source}` to respond (regarding {sourceRegarding}), before handing off the next request to tmux session `{target}` (regarding {targetRegarding})', 'YO!agent handoff wait text names both sessions and both regarding summaries');
  });

  test('t@7287', () => {
    const api = loadYolomux('', ['1', '2']);
    api.setDocumentTitleNowForTest(200000);
    api.setTmuxSignalStateForTest({
      windows: [
        {
          key: '1:0',
          session: '1',
          window_index: '0',
          activity_ts: 190,
          bell_flag: true,
          silence_flag: true,
          active_clients: 2,
          active_clients_list: 'client-a,client-b',
          active_client_details: [{name: 'client-a', user: 'keiven'}, {name: 'client-b', user: 'viewer'}],
          zoomed: true,
          layout: 'layout-host',
          visible_layout: 'visible-layout-host',
          panes: [{
            window_key: '1:0',
            session: '1',
            window_index: '0',
            pane_index: '0',
            target: '%11',
            pane_id: '%11',
            current_path: '/home/keivenc/live-project',
            current_command: 'codex',
            mode: 'copy-mode',
            in_mode: true,
            input_off: true,
            synchronized: true,
            dead: true,
            dead_status: 9,
          }],
        },
        {
          key: '2:0',
          session: '2',
          window_index: '0',
          activity_ts: 10,
          panes: [{
            window_key: '2:0',
            session: '2',
            window_index: '0',
            pane_index: '0',
            target: '%22',
            pane_id: '%22',
            current_path: '/home/keivenc/old-project',
            current_command: 'claude',
          }],
        },
      ],
    });
    api.setActivitySummaryPayloadForTest({
      agents: [{
        label: "session '2' 0:claude",
        session: '2',
        window: '0',
        window_label: '0:claude',
        pane: '0',
        pane_target: '%22',
        agent_kind: 'claude',
        cwd: '/home/keivenc/old-project',
        transcript: '/tmp/claude.jsonl',
        sort_ts: 999,
      }, {
        label: "session '1' 0:codex",
        session: '1',
        window: '0',
        window_label: '0:codex',
        pane: '0',
        pane_target: '%11',
        agent_kind: 'codex',
        cwd: '/home/keivenc/project',
        transcript: '/tmp/codex.jsonl',
      }],
    });
    const html = api.yoagentRecentAgentsHtmlForTest();
    assert.ok(html.includes('agent exited (status 9)'), 'recent agents surface dead tmux agent status');
    assert.ok(html.indexOf('session 1') < html.indexOf('session 2'), 'tmux sub-window activity sorts recent agents ahead of stale backend order');
    assert.ok(html.includes('yoagent-recent-agent tmux-idle'), 'old tmux sub-window activity dims idle recent-agent rows');
    assert.ok(html.includes('signal-bell') && html.includes('signal-silence'), 'recent agents surface tmux bell and silence signal chips');
    assert.ok(html.includes('signal-presence') && html.includes('2 viewers'), 'recent agents surface tmux active-client presence');
    assert.ok(html.includes('signal-zoom') && html.includes('zoom'), 'recent agents surface tmux zoom state');
    assert.ok(html.includes('/home/keivenc/live-project'), 'recent agents prefer live tmux pane_current_path');
    assert.ok(html.includes('client-a') && html.includes('layout-host') && html.includes('visible-layout-host'), 'recent agent title includes tmux viewer and layout details');
    assert.ok(html.includes('copy-mode') && html.includes('read-only') && html.includes('sync'), 'recent agents surface pane mode/read-only/sync chips');
    assert.ok(html.includes('data-yolomux-agent-restart="codex"'), 'dead agent row offers a restart action for the same agent kind');
    assert.ok(html.includes('tmux'), 'recent agents prefer tmux recency text when window_activity is available');
  });

  test('t@7290', () => {
    const api = loadYolomux('', ['1', '2']);
    api.rememberFileExplorerOpenIntentForTest(false);
    const single = api.emptyLayoutSlots();
    single[api.layoutTreeKey] = api.leafNode('left');
    single.left = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(single);
    assert.equal(api.rightmostExistingPaneSlot(), null, 'single-pane layout has no existing right pane');
    api.openYoagentRightPane();
    let serialized = api.serialize(api.currentSlots());
    const paneList = value => Object.values(value.panes).filter(Boolean).map(canonical);
    const hasPane = (panes, expected) => panes.some(pane => JSON.stringify(pane) === JSON.stringify(expected));
    assert.equal(serialized.tree.split, 'row', 'Cmd+Alt+B creates a right pane from a single-pane layout');
    assert.ok(hasPane(paneList(serialized), {tabs: ['1'], active: '1'}), 'single-pane shortcut keeps the tmux tab alone');
    assert.ok(hasPane(paneList(serialized), {tabs: ['__yoagent__'], active: '__yoagent__'}), 'single-pane shortcut creates a separate YO!agent pane');

    api.rememberFileExplorerOpenIntentForTest(true);
    const finderSingle = api.emptyLayoutSlots();
    finderSingle[api.layoutTreeKey] = api.splitNode('row', api.leafNode('slot1'), api.leafNode('left'), 22);
    finderSingle.slot1 = api.paneStateWithTabs([api.fileExplorerItemId], api.fileExplorerItemId);
    finderSingle.left = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(finderSingle);
    assert.equal(api.rightmostExistingPaneSlot(), null, 'Finder plus one content pane does not count as an existing right pane');
    api.openYoagentRightPane();
    serialized = api.serialize(api.currentSlots());
    const finderSinglePanes = Object.values(serialized.panes).map(canonical);
    assert.ok(hasPane(finderSinglePanes, {tabs: ['1'], active: '1'}), 'Finder-docked single content pane keeps the tmux tab alone');
    assert.ok(hasPane(finderSinglePanes, {tabs: ['__files__'], active: '__files__'}), 'Finder-docked single content pane keeps Finder alone');
    assert.ok(hasPane(finderSinglePanes, {tabs: ['__yoagent__'], active: '__yoagent__'}), 'Finder-docked single content pane creates a separate YO!agent pane');

    api.rememberFileExplorerOpenIntentForTest(false);
    const split = api.emptyLayoutSlots();
    split[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
    split.left = api.paneStateWithTabs(['2', '__yoagent__'], '2');
    split.slot1 = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(split);
    assert.equal(api.rightmostExistingPaneSlot(), 'slot1', 'right-pane detection uses the layout tree, not only literal right slot names');
    api.openYoagentRightPane();
    serialized = api.serialize(api.currentSlots());
    const splitPanes = paneList(serialized);
    assert.ok(hasPane(splitPanes, {tabs: ['2'], active: '2'}), `existing split removes YO!agent from the source pane: ${JSON.stringify(splitPanes)}`);
    assert.ok(hasPane(splitPanes, {tabs: ['1', '__yoagent__'], active: '__yoagent__'}), `existing split places YO!agent into the right pane: ${JSON.stringify(splitPanes)}`);
  });

  test('t@7321', () => {
    // file-search dedupe folds mirror + symlink copies, keeps different-content same-name.
    const api = loadYolomux('', ['1']);
    const deduped = api.dedupeFileSearchResults([
      {path: '/a/notes/DIS-1842.md', realpath: '/a/notes/DIS-1842.md', size: 100},
      {path: '/b/notes/DIS-1842.md', realpath: '/b/notes/DIS-1842.md', size: 100},   // content mirror -> folded
      {path: '/c/DIS-1842.md', realpath: '/c/DIS-1842.md', size: 250},                // different content -> kept
      {path: '/d/link.md', realpath: '/a/notes/DIS-1842.md', size: 100},              // symlink overlap -> folded
    ]).map(file => file.path);
    assert.deepStrictEqual([...deduped], ['/a/notes/DIS-1842.md', '/c/DIS-1842.md'], '#25: mirror + symlink copies fold; different-content same-name both survive');
    // Unknown-size hits dedupe only by path/realpath (never collapse two same-name unknown-size files).
    const unknown = api.dedupeFileSearchResults([
      {path: '/x/a.md', realpath: '/x/a.md'},
      {path: '/y/a.md', realpath: '/y/a.md'},
    ]).map(file => file.path);
    assert.deepStrictEqual([...unknown], ['/x/a.md', '/y/a.md'], '#25: unknown-size same-name files are not collapsed');
  });

  test('t@7339', () => {
    // the yoagent markdown normalizer tightens loose lists / collapses blank-line runs.
    const api = loadYolomux('', ['1']);
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.equal(api.yoagentTightMarkdown('- a\n\n- b\n\n- c'), '- a\n- b\n- c', '#129: blank lines between adjacent list items are stripped (tight list)');
    assert.equal(api.yoagentTightMarkdown('1. a\n\n2. b'), '1. a\n2. b', '#129: ordered-list item gaps are stripped too');
    assert.equal(api.yoagentTightMarkdown('lead\n\n\n\nmore'), 'lead\n\nmore', '#129: runs of 2+ blank lines collapse to one');
    assert.equal(api.yoagentTightMarkdown('- a\n\nparagraph'), '- a\n\nparagraph', '#129: a blank line before a NON-list paragraph is preserved');
    // The chat assistant path also runs the tightener (not just the summary path).
    assert.ok(/renderMarkdownPreviewInto\(body, yoagentTightMarkdown\(/.test(source), '#129: the chat assistant body is tightened before rendering');
    // yoagentInlineMarkdown folds in the tightening (heading downgrade + tight lists).
    assert.equal(api.yoagentInlineMarkdown('## H\n\n- a\n\n- b'), '**H**\n\n- a\n- b', '#129: inline-markdown downgrades headings AND tightens the list');
    // a <p> inside an <li> carries no margin so loose lists render tight.
    assert.ok(/\.markdown-body li > p\s*\{[^}]*margin:\s*0/.test(fs.readFileSync('static/yolomux.css', 'utf8')), '#128: .markdown-body li > p has zero margin');
  });

  test('t@7355', () => {
    // the markdown-preview relative-link path normalizer + the in-pane link handler.
    const api = loadYolomux('', ['1']);
    assert.equal(api.joinAndNormalize('/a/b/c', './x.md'), '/a/b/c/x.md', '#133: ./ resolves against the base dir');
    assert.equal(api.joinAndNormalize('/a/b/c', '../y/z.md'), '/a/b/y/z.md', '#133: ../ pops a segment');
    assert.equal(api.joinAndNormalize('/a/b', 'bare.md'), '/a/b/bare.md', '#133: a bare name resolves against the base dir');
    assert.equal(api.joinAndNormalize('/a/b', '/abs/x.md'), '/abs/x.md', '#133: an absolute rel ignores the base');
    assert.equal(api.joinAndNormalize('/a/b/c', '../../top.md'), '/a/top.md', '#133: multiple ../ collapse');
    const src = fs.readFileSync('static/yolomux.js', 'utf8');
    // The handler reads the RAW href, opens external links in a new tab, and routes file:// + relative
    // file links through openFileInEditor with a preview/edit mode + a failure toast.
    assert.ok(/function handleMarkdownPreviewLinkClick/.test(src), '#133: the markdown-preview link handler exists');
    assert.ok(/a\.getAttribute\('href'\)/.test(src), '#133: the handler reads the raw href attribute');
    assert.ok(/function localPathFromFileHref/.test(src), '#133: file:// preview links are converted to server-side paths');
    assert.ok(src.indexOf('localPathFromFileHref(href)') > -1, '#133: file:// links use the local-path helper');
    assert.ok(src.indexOf('localPathFromFileHref(href)') < src.indexOf("window.open(a.href, '_blank', 'noopener,noreferrer')"), '#133: file:// links are handled before the external window.open branch');
    assert.ok(/window\.open\(a\.href, '_blank', 'noopener,noreferrer'\)/.test(src), '#133: external/other-scheme links open in a new tab');
    assert.ok(/openFileInEditor\(resolved, basenameOf\(resolved\), \{[\s\S]*?viewMode: editorPreviewModeAvailable\(resolved\) \? 'preview' : 'edit'/.test(src), '#133: preview-capable file links open in preview (md/html), else edit');
    assert.ok(/t\('preview\.openFailed'/.test(src), "#133: a failed open surfaces a toast");
    // The handler is wired ONLY to the file-editor preview (path provided), not to yoagent bodies.
    assert.ok(/renderMarkdownPreviewInto\(container, text, path, \{context: previewContext\}\)/.test(src), '#133: the file-editor preview threads the owning path and preview context; yoagent bodies pass no path');
  });

  test('t@7378', () => {
    // #260: the Global color theme field renders plain RADIO buttons (replaced the macOS-style cards).
    const api = loadYolomux('', ['1']);
    api.setActiveLocaleForTest('en');
    api.setClientSettingsPatchForTest({appearance: {theme: 'light'}});
    const html = api.preferencesPanelHtmlForTest('');
    for (const v of ['system', 'dark', 'light']) {
      assert.ok(new RegExp(`<input type="radio"[^>]*value="${v}"[^>]*data-setting-path="appearance\\.theme"`).test(html), `#260: a ${v} theme radio renders`);
    }
    assert.ok(/role="radiogroup"/.test(html), '#260: the theme radios render as a radiogroup');
    assert.ok(/value="light"[^>]*data-setting-path="appearance\.theme"[^>]*checked/.test(html), '#260: the active theme (light) radio is checked');
    assert.equal((html.match(/type="radio"[^>]*data-setting-path="appearance\.theme"[^>]*checked/g) || []).length, 1, '#260: exactly one theme radio is checked');
    assert.equal(html.includes('data-theme-card'), false, '#260: no macOS-style theme-card markup remains');
    assert.equal(/<select[^>]*data-setting-path="appearance\.theme"/.test(html), false, '#260: the theme field is radios, not a <select>');
    const themeSrc = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/if \(path === 'appearance\.theme'\) \{\s*globalThemeMode = normalizeGlobalThemeMode\(value\);\s*applyGlobalThemeMode/.test(themeSrc), '#260: changing the theme radio applies the theme live (via savePreferenceControl)');
    const themeCss = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/\.preferences-radio-group\s*\{/.test(themeCss), '#260: the radio group has styling');
    assert.equal(/\.theme-card-system/.test(themeCss), false, '#260: the old theme-card CSS is gone');
  });

  test('t@7399', () => {
    // Preview font size is independent from the editor font size and defaults one px larger.
    const api = loadYolomux('', ['1']);
    api.setActiveLocaleForTest('en');
    const html = api.preferencesPanelHtmlForTest('');
    assert.ok(/data-preference-section="Terminal \/ Editor"[\s\S]*data-setting-path="appearance\.preview_font_size"/.test(html), 'preview font size renders in Terminal / Editor preferences');
    assert.ok(html.includes('Preview font size'), 'preview font size preference has a label');
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(source.includes("let editorPreviewFontSize = initialSetting('appearance.preview_font_size', editorFontSize + 1);"), 'preview font size defaults one larger than editor font during bootstrap');
    assert.ok(source.includes("root.setProperty('--editor-preview-font-size'"), 'preview font size writes its own CSS variable');
    assert.ok(source.includes("numberSetting('appearance.preview_font_size', editorFontSize + 1)"), 'preview font size reload preserves the editor+1 fallback');
    assert.ok(source.includes('class="file-editor-preview-font-panel"'), 'preview toolbar includes a font-size control group');
    assert.ok(source.includes('data-editor-preview-font-step="-1"'), 'preview toolbar includes a decrease button');
    assert.ok(source.includes('data-editor-preview-font-step="1"'), 'preview toolbar includes an increase button');
    assert.ok(source.includes("saveSettingsPatch(settingPatch('appearance.preview_font_size', next))"), 'preview font toolbar persists the setting');
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/\.file-editor-preview-pane\s*\{[^}]*font-size:\s*var\(--editor-preview-font-size\)/.test(css), 'rendered preview pane uses the preview font variable');
    assert.ok(/\.file-editor-preview-pane-panel\s*\{[^}]*font-size:\s*var\(--editor-preview-font-size\)/.test(css), 'split/preview pane uses the preview font variable');
    assert.ok(/\.file-editor-raw-panel\s*\{[^}]*font-size:\s*var\(--editor-font-size\)/.test(css), 'raw editor pane keeps the editor font variable');
    const settingsSource = fs.readFileSync('yolomux_lib/settings.py', 'utf8');
    assert.ok(settingsSource.includes('"preview_font_size": 14'), 'preview font size default is 14');
    assert.ok(settingsSource.includes('("appearance", "preview_font_size"): (6, 32)'), 'preview font size has server-side limits');
  });

  test('t@mv4', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/const SETTING_FALLBACKS = Object\.freeze\(\{[\s\S]*'terminal_editor\.scrollback': 5000,[\s\S]*'uploads\.max_bytes': 300 \* 1024 \* 1024,[\s\S]*\}\);/.test(source), 'shared setting fallback table owns static client fallbacks');
    assert.ok(/function initialSetting\(path, fallback\)[\s\S]*settingFallback\(path\)/.test(source), 'initialSetting reads the shared fallback table when no fallback is passed');
    assert.ok(source.includes("let terminalScrollback = initialSetting('terminal_editor.scrollback');"), 'bootstrap terminal scrollback reads the shared fallback');
    assert.ok(source.includes("terminalScrollback = numberSetting('terminal_editor.scrollback');"), 'settings reload terminal scrollback reads the shared fallback');
    for (const oldCall of [
      "initialSetting('appearance.date_time_hour_cycle', '24')",
      "numberSetting('appearance.terminal_font_size', 13)",
      "initialSetting('appearance.terminal_font_size', 13)",
      "numberSetting('appearance.editor_font_size', 13)",
      "initialSetting('appearance.editor_font_size', 13)",
      "numberSetting('appearance.file_explorer_font_size', 13)",
      "initialSetting('appearance.file_explorer_font_size', 13)",
      "numberSetting('editor.autosave_delay_seconds', 2.5)",
      "initialSetting('editor.autosave_delay_seconds', 2.5)",
      "numberSetting('file_explorer.image_preview_max_px', 320)",
      "initialSetting('file_explorer.image_preview_max_px', 320)",
      "initialSetting('file_explorer.image_open_mode', 'same-tab')",
      "numberSetting('terminal_editor.scrollback', 5000)",
      "initialSetting('terminal_editor.scrollback', 5000)",
      "numberSetting('uploads.max_bytes', 300 * 1024 * 1024)",
      "initialSetting('uploads.max_bytes', 300 * 1024 * 1024)",
    ]) {
      assert.equal(source.includes(oldCall), false, `${oldCall} is routed through SETTING_FALLBACKS`);
    }
  });

  test('t@mv5', () => {
    const bootSource = fs.readFileSync('static_src/js/yolomux/00_bootstrap_state.js', 'utf8');
    const coreSource = fs.readFileSync('static_src/js/yolomux/10_core_utils.js', 'utf8');
    const settingsSource = fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8');
    const dockviewSource = fs.readFileSync('static_src/js/yolomux/75_dockview_layout.js', 'utf8');
    const popoutSource = fs.readFileSync('static_src/js/yolomux/94_preview_popout.js', 'utf8');
    assert.ok(/const THEME_BODY_CLASSES = Object\.freeze\(\[[\s\S]*Object\.values\(THEME_CLASS_BY_MODE\)[\s\S]*Object\.values\(THEME_RESOLVED_CLASS_BY_MODE\)/.test(bootSource), 'theme body classes have one shared owner');
    assert.ok(/const EDITOR_THEME_BODY_CLASSES = Object\.freeze\(Object\.values\(EDITOR_THEME_CLASS_BY_MODE\)\)/.test(bootSource), 'editor theme body classes have one shared owner');
    assert.ok(/const PREVIEW_POPOUT_BODY_CLASSES = Object\.freeze\(\[[\s\S]*EDITOR_PREVIEW_VANILLA_CLASS/.test(bootSource), 'preview popouts preserve classes from the shared owners');
    assert.ok(/function themeBodyClass\(mode\)[\s\S]*THEME_CLASS_BY_MODE/.test(coreSource), 'theme body class lookup goes through a helper');
    assert.ok(/function editorThemeBodyClass\(mode\)[\s\S]*EDITOR_THEME_CLASS_BY_MODE/.test(coreSource), 'editor theme body class lookup goes through a helper');
    assert.ok(settingsSource.includes('document.body?.classList.remove(...THEME_BODY_CLASSES);'), 'global theme removes all app theme classes through the shared list');
    assert.ok(settingsSource.includes('document.body?.classList.add(themeBodyClass(resolved), themeResolvedBodyClass(resolved));'), 'global theme adds app theme classes through helpers');
    assert.ok(settingsSource.includes('document.body?.classList.remove(...EDITOR_THEME_BODY_CLASSES, EDITOR_PREVIEW_VANILLA_CLASS);'), 'editor theme removes editor body classes through the shared list');
    assert.ok(settingsSource.includes("document.body?.classList.add(editorThemeBodyClass(scheme.dark ? 'dark' : 'light'));"), 'editor theme adds body classes through the helper');
    assert.ok(dockviewSource.includes("contains(themeBodyClass('light'))"), 'Dockview chooses the light theme through the shared class helper');
    assert.ok(popoutSource.includes('PREVIEW_POPOUT_BODY_CLASSES.filter'), 'preview popouts preserve theme classes through the shared list');
    for (const oldCall of [
      "button.classList.toggle('theme-dark'",
      "button.classList.toggle('theme-light'",
      "document.body?.classList.remove('editor-theme-system', 'editor-theme-dark', 'editor-theme-light', 'editor-preview-vanilla')",
      "document.body?.classList.add(scheme.dark ? 'editor-theme-dark' : 'editor-theme-light')",
      "document.body?.classList.toggle('editor-preview-vanilla'",
      "document.body?.classList.remove('theme-system', 'theme-dark', 'theme-light', 'theme-resolved-dark', 'theme-resolved-light')",
      "document.body?.classList.add(`theme-${resolved}`, `theme-resolved-${resolved}`)",
      "document.body?.classList.add('theme-system')",
      "document.body?.classList?.contains('theme-light')",
      "const keep = ['theme-light', 'theme-dark', 'editor-theme-light', 'editor-theme-dark', 'editor-preview-vanilla'];",
    ]) {
      assert.equal(`${settingsSource}\n${dockviewSource}\n${popoutSource}`.includes(oldCall), false, `${oldCall} routes through theme class owners`);
    }
  });

  test('t@7423', () => {
    // Phase 1: the topbar language switcher + system-locale resolution.
    const api = loadYolomux('', ['1']);
    // Explicit prefs resolve to themselves; 'system' (no navigator.language in the harness) falls back to en.
    assert.equal(api.resolveLocalePref('zh-Hant'), 'zh-Hant', 'Phase 1: an explicit locale pref resolves to itself');
    assert.equal(api.resolveLocalePref('zh-Hans'), 'zh-Hans', 'Phase 1: Simplified Chinese resolves to itself');
    assert.equal(api.resolveLocalePref('en'), 'en', 'Phase 1: English resolves to itself');
    assert.equal(api.resolveLocalePref('system'), 'en', 'Phase 1: system falls back to en without a browser locale');
    // The switcher choices: system + shipped locales in product-priority order + pseudo, endonym-labeled.
    const choices = api.i18nLocaleChoices();
    assert.deepEqual(choices.map(c => c.value), ['system', 'en', 'zh-Hant', 'zh-Hans', 'ja', 'ko', 'es', 'de', 'fr', 'it', 'pt-BR', 'pl', 'nl', 'he', 'ar', 'ru', 'hi', 'vi', 'th', 'tr', 'en-XA'], 'Phase 1/2/4: the locale choices are ordered with all shipped locales then pseudo');
    assert.equal(choices.find(c => c.value === 'de').label, 'Deutsch', 'Phase 2: German is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'ru').label, 'Русский', 'Phase 2: Russian is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'ar').label, 'العربية', 'Phase 2: Arabic is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'he').label, 'עברית', 'Hebrew is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'vi').label, 'Tiếng Việt', 'Vietnamese is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'th').label, 'ไทย', 'Thai is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'tr').label, 'Türkçe', 'Turkish is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'nl').label, 'Nederlands', 'Dutch is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'pl').label, 'Polski', 'Polish is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'it').label, 'Italiano', 'Italian is labeled with its endonym');
    for (const loc of ['de', 'fr', 'pt-BR', 'ru', 'ko', 'hi', 'ar', 'he', 'vi', 'th', 'tr', 'nl', 'pl', 'it']) {
      assert.equal(api.resolveLocalePref(loc), loc, `Phase 2: ${loc} resolves to itself`);
    }
    // RTL: Arabic and Hebrew are detected as right-to-left; LTR locales are not.
    assert.equal(api.i18nIsRtl('ar'), true, 'Phase 2: ar is RTL');
    assert.equal(api.i18nIsRtl('he'), true, 'he is RTL');
    assert.equal(api.i18nIsRtl('de'), false, 'Phase 2: de is LTR');
    // applyLocale flips document.dir; the build CSS uses logical flow properties so RTL mirrors.
    const rtlSrc = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/document\.documentElement\.setAttribute\('dir', i18nIsRtl\(next\) \? 'rtl' : 'ltr'\)/.test(rtlSrc), 'Phase 2: applyLocale sets the document direction for RTL locales');
    // A language switch must repaint the Finder's static toolbar chrome, not just panel bodies — so
    // rerenderForLocale rebuilds the Finder panel from source (fixes stale prev-locale toolbar labels).
    assert.ok(/function rerenderForLocale[\s\S]*?relocalizeFileExplorerPanels\(\)/.test(rtlSrc), 'rerenderForLocale rebuilds the Finder toolbar chrome on a language switch');
    assert.ok(/function relocalizeFileExplorerPanels\(\)[\s\S]*?removePanelForItem\(fileExplorerItemId\)[\s\S]*?dockviewRemountPanel\(fileExplorerItemId\)[\s\S]*?renderPanels\(/.test(rtlSrc), 'relocalizeFileExplorerPanels evicts then remounts the Finder panel through its shared renderer');
    assert.ok(/function dockviewRemountPanel\(item\)[\s\S]*?getPanel\?\.\(item\)[\s\S]*?updateParameters/.test(rtlSrc), 'Dockview panel replacement reuses the registered renderer update path when the layout signature is unchanged');
    const rtlCss = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.equal(/(^|[^-])(margin|padding|border)-(left|right):/m.test(rtlCss.replace(/[a-z-]*-(left|right)-radius/g, '')), false, 'Phase 2: flow-spacing CSS uses logical (inline) properties, not physical left/right, so RTL mirrors');
    assert.ok(rtlCss.includes('margin-inline-start:') && rtlCss.includes('padding-inline-start:'), 'Phase 2: the CSS uses logical inline properties');
    assert.equal(choices.find(c => c.value === 'es').label, 'Español', 'Phase 1: Spanish is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'ja').label, '日本語', 'Phase 1: Japanese is labeled with its endonym');
    assert.ok(choices.findIndex(c => c.value === 'ko') === choices.findIndex(c => c.value === 'ja') + 1, 'Korean appears immediately after Japanese');
    assert.deepEqual(['de', 'fr', 'it', 'pt-BR', 'pl', 'nl'].map(loc => choices[choices.findIndex(c => c.value === 'de') + ['de', 'fr', 'it', 'pt-BR', 'pl', 'nl'].indexOf(loc)]?.value), ['de', 'fr', 'it', 'pt-BR', 'pl', 'nl'], 'German, French, Italian, Portuguese, Polish, and Dutch are grouped together');
    assert.ok(choices.findIndex(c => c.value === 'he') === choices.findIndex(c => c.value === 'nl') + 1, 'Hebrew appears immediately after Dutch');
    assert.equal(api.resolveLocalePref('es'), 'es', 'Phase 1: Spanish resolves to itself');
    assert.equal(api.resolveLocalePref('ja'), 'ja', 'Phase 1: Japanese resolves to itself');
    assert.equal(choices.find(c => c.value === 'zh-Hant').label, '繁體中文', 'Phase 1: Traditional Chinese is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'zh-Hans').label, '简体中文', 'Phase 1: Simplified Chinese is labeled with its endonym');
    const src = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/sessionButtons\.appendChild\(createTopbarLanguageSwitcher\(\)\)/.test(src), 'Phase 1: the topbar renders the language switcher');
    assert.ok(/function createTopbarLanguageSwitcher[\s\S]*?applyLocale\(resolveLocalePref\(value\)\)[\s\S]*?saveSettingsPatch\(settingPatch\('general\.language', value\)\)/.test(src), 'Phase 1: the switcher applies the locale optimistically AND saves general.language (same setting as Preferences)');
    assert.ok(/function rerenderForLocale[\s\S]*?renderSessionButtons\(\{force: true\}\)/.test(src), 'Phase 1: a real locale switch force-repaints the topbar labels after selection');
    assert.ok(src.includes("active.matches?.('select, input, .topbar-language, .app-menu-button')") && /function renderSessionButtons[\s\S]*?topbarControlIsActive\(\)/.test(src), 'the topbar does not rebuild while a topbar control is focused/open');
    // The zh fallback mapping (zh-TW/HK/Hant -> Hant, other zh -> Hans).
    assert.ok(/nav\.startsWith\('zh'\)\) return \/hant\|/.test(src), 'Phase 1: system maps Chinese browser locales to Hant/Hans');
    assert.ok(/share\.ttl_seconds[\s\S]*suffix:\s*t\('unit\.minute\.short'\)/.test(src), 'YO!share Preferences minute suffix is localized');
    assert.ok(/function tmuxSessionNameError\(name\)[\s\S]*rename\.error\.required[\s\S]*rename\.error\.tooLong[\s\S]*rename\.error\.invalidChars/.test(src), 'session rename validation errors use locale keys');
    assert.ok(/function dropActionDisplayLabel\(action\)[\s\S]*action\.labelKey[\s\S]*t\(action\.labelKey\)/.test(src), 'drop action display labels use locale keys while canonical labels remain stable');
    assert.ok(/function showTerminalDropSuggestions[\s\S]*t\('drop\.pathInserted'\)[\s\S]*tPlural\('drop\.files'[\s\S]*t\('drop\.suggestionHint'/.test(src), 'terminal drop suggestion header is localized');
    assert.ok(/async function showContext\(session\)[\s\S]*transcript\.tailTitle/.test(src), 'transcript tail modal title is localized');
    assert.ok(/\.topbar-language\s*\{/.test(fs.readFileSync('static/yolomux.css', 'utf8')), 'Phase 1: the language switcher has topbar styling');
    // #256: topbar theme switcher (auto/dark/light) mirrors the language switcher and sits right of it;
    // order ends Language, Theme, Activity (activity pinned far-right).
    // #257: the topbar theme switcher was REMOVED (redundant). Order is Language, Ownership, then Activity (far right).
    assert.ok(/sessionButtons\.appendChild\(createTopbarLanguageSwitcher\(\)\);\s*sessionButtons\.appendChild\(createTopbarOwnerStatus\(\)\);\s*sessionButtons\.appendChild\(createTopbarActivityStatus\(\)\)/.test(src), '#257: topbar order is Language, Ownership, then Activity (no theme switcher between them)');
    assert.ok(/function topbarControlIsActive\(\)[\s\S]*document\.activeElement[\s\S]*sessionButtons\?\.contains\(active\)[\s\S]*active\.matches\?\.\('select, input, \.topbar-language, \.app-menu-button'\)/.test(src), '#62: topbar detects focused controls before passive rebuilds');
    assert.ok(/if \(!options\.force && topbarControlIsActive\(\)\) \{[\s\S]*pendingSessionButtonsRender = true[\s\S]*return;\s*\}/.test(src), '#62: passive topbar renders defer while a topbar control is focused');
    assert.ok(/button\.addEventListener\('blur', flushPendingSessionButtonsRender\)/.test(src), '#62: language button blur flushes a deferred topbar render');
    assert.equal(/createTopbarThemeSwitcher/.test(src), false, '#257: createTopbarThemeSwitcher is gone (no redundant topbar theme select)');
    {
      const css = fs.readFileSync('static/yolomux.css', 'utf8');
      assert.equal(/\.topbar-theme\s*\{/.test(css), false, '#257: the .topbar-theme CSS is removed with the switcher');
      // #254/#259 follow-up: light-mode inactive-pane dim stays neutral gray, with a softer alpha.
      assert.ok(css.includes('--inactive-pane-overlay-rgb: 90 96 105'), '#259: light-mode inactive panes dim a softer neutral gray (no red cast)');
      assert.ok(css.includes('--inactive-pane-overlay-alpha: 0.09'), '#259: light-mode inactive panes keep the softer alpha base');
      // Light-mode pane header (image 043): greenish-light tab-strip container + light frame-control
      // buttons (the minimize/zoom squares used to render dark/"black" with no light values).
      assert.ok(/body\.theme-light\s*\{[\s\S]*?--active-accent-dim:\s*#e1edda/.test(css), 'light mode: the pane tab-strip container is greenish-light (active-accent-dim)');
      assert.ok(/body\.theme-light\s*\{[\s\S]*?--active-accent-text:\s*#071000/.test(css), 'light mode: active accent text is dark on bright green/gold/white fills');
      assert.ok(/body\.theme-light\s*\{[\s\S]*?--icon-code:\s*#0369a1[\s\S]*?--link-soft:\s*#075985/.test(css), 'light mode: code/link text tokens use darker readable shades');
      assert.ok(/body\.theme-light\s*\{[\s\S]*?--pane-tab-control-bg:\s*#f7f9fc/.test(css), 'light mode: the pane minimize/frame button has a light fill (not a dark square)');
      assert.ok(/body\.theme-light\s*\{[\s\S]*?--pane-tab-zoom-bg:\s*var\(--active-control-bg\)/.test(css), 'light mode: the pane zoom button uses the shared active-control fill, not a dark square');
      assert.ok(/function uiColorVisualPreset\(value, light = false\)[\s\S]*value === 'green'[\s\S]*light[\s\S]*text:\s*'#071000'/.test(src), 'Green active-color metadata reports dark on-accent text for the light preset');
      assert.equal(css.includes('--inactive-pane-overlay-rgb: 124 82 88'), false, '#259: the earlier warm/red tint is gone (superseded by gray)');
      assert.equal(css.includes('--inactive-pane-overlay-alpha: 0.16'), false, '#259 follow-up: the too-dark light overlay alpha is gone');
      assert.equal(css.includes('--inactive-pane-overlay-alpha: 0.13'), false, '#259 follow-up: the still-too-dark light overlay alpha is gone');
      // #258 follow-up: editor toolbar placement is inherited from stable parent zones, not per-button order.
      assert.ok(/\.file-editor-toolbar-left\s*\{[^}]*flex:\s*1 1 auto/.test(css), 'editor info bar: #/Differ/FROM-TO/path live in the shared left zone');
      assert.ok(/\.file-editor-toolbar-center\s*\{[^}]*position:\s*absolute[\s\S]*left:\s*50%/.test(css), 'editor info bar: font-size controls live in the shared center zone');
      assert.ok(/\.file-editor-toolbar-right\s*\{[^}]*margin-inline-start:\s*auto[\s\S]*justify-content:\s*flex-end/.test(css), 'editor info bar: edit/preview/tools live in the shared right zone');
      assert.ok(/className:\s*'file-editor-path'[\s\S]*attributes:\s*\{dir:\s*'ltr'\}/.test(src), 'editor info bar: the file toolbar includes an absolute path text slot');
      assert.ok(/const pathNode = panel\.querySelector\('\.file-editor-path'\);[\s\S]*pathNode\.textContent = compactHomePath\(path\) \|\| path;[\s\S]*pathNode\.title = path;/.test(src), 'editor info bar: the file path slot shows a home-compacted path and titles the absolute path');
      assert.ok(/\.file-editor-path\s*\{[^}]*flex:\s*1 1 min\(42vw,\s*72ch\)[\s\S]*direction:\s*ltr/.test(css), 'editor info bar: absolute paths claim toolbar width and render left-to-right');
      assert.equal(/\.file-editor-(?:gutter|diff|diff-expand)-panel\s*\{[^}]*order:/.test(css), false, 'editor info bar: left buttons do not own placement with child order rules');
      assert.ok(/\.file-editor-diff-ref-panel\s*\{[^}]*min-width:\s*max-content[^}]*overflow:\s*visible/.test(css), 'editor info bar: FROM/TO/reset is intrinsic-width and not clipped');
      assert.equal(css.includes('max-width: min(32vw, 190px)'), false, 'editor info bar: the old too-narrow 190px clipping cap is gone');
      assert.equal(/\.file-editor-diff-ref-panel \.diff-ref-controls\.compact \.diff-ref-input,[\s\S]*width:\s*38px/.test(css), false, 'editor info bar: compact diff refs are not hard-capped before the /HEAD suffix');
      assert.ok(/\.file-editor-diff-ref-panel \.diff-ref-controls\.compact \[data-diff-ref-from\]\s*\{\s*width:\s*clamp\(16ch, 18vw, 24ch\)/.test(css), 'editor info bar: compact FROM refs reserve the full /HEAD suffix');
      assert.ok(/\.file-editor-diff-ref-panel \.diff-ref-controls\.compact \[data-diff-ref-to\]\s*\{\s*width:\s*9ch/.test(css), 'editor info bar: compact TO refs retain a short-width control');
      assert.ok(/\.file-editor-toolbar\s*\{[^}]*container-type:\s*inline-size/.test(css), 'editor info bar: FROM/TO/reset expansion keys off toolbar width, not the browser viewport');
      assert.ok(/@container \(min-width: 900px\)\s*\{[\s\S]*\.file-editor-diff-ref-panel\s*\{[^}]*max-width:\s*min\(72vw,\s*620px\)/.test(css), 'editor info bar: the compact comparison panel remains bounded on desktop toolbars');
      assert.ok(/\.file-tab-parent\s*\{[^}]*text-overflow:\s*ellipsis/.test(css), 'duplicate file-tab parent suffix is styled as compact muted metadata');
      assert.ok(/\.preferences-setting-control\.setting-type-select,\s*\.preferences-setting-control\.setting-type-text\s*\{[^}]*justify-content:\s*start/.test(css), 'Preferences selects/text inputs are left-aligned from the shared inset');
      assert.ok(/\.preferences-setting-control\.setting-type-number input\[type="number"\]\s*\{[^}]*margin-inline-start:\s*var\(--preferences-control-left-indent\)/.test(css), 'Preferences number inputs are left-aligned from the shared inset');
      // #258 (toast): the toast stack clears the topbar (z-index above 180) and messages wrap, not clip.
      assert.ok(/\.panel-toast-stack\s*\{[^}]*z-index:\s*var\(--z-full-screen-overlay\)/.test(css), '#258: the toast stack renders above the topbar (var(--z-full-screen-overlay)) so it is not clipped under it');
      assert.ok(/\.toast-line\s*\{[^}]*white-space:\s*normal/.test(css), '#258: toast messages wrap (white-space:normal) instead of ellipsis-clipping');
      assert.equal(/\.toast-line\s*\{[^}]*white-space:\s*nowrap/.test(css), false, '#258: the old nowrap/ellipsis clipping of the toast message line is gone');
    }
    // #255: inactive-pane dimming is now ONE CSS rule keyed off the uniformly-toggled .focused-pane class
    // — no per-pane JS overlay, no isVirtualItem special-case, every pane type dims identically.
    assert.equal(/function installPanelInactiveOverlays/.test(src), false, '#255: the per-pane JS overlay installer is deleted (dimming is pure CSS)');
    assert.equal(/class="panel-inactive-overlay"/.test(src), false, '#255: no per-pane inactive-overlay div is injected anymore');
    assert.ok(/\.panel:not\(\.focused-pane\)[^{]*\.panel-overlay-root::after\s*\{[^}]*background:\s*var\(--inactive-pane-overlay\)/.test(fs.readFileSync('static/yolomux.css', 'utf8')), '#255: inactive panes dim via one CSS rule on .panel:not(.focused-pane) .panel-overlay-root::after');
    assert.equal(/\.panel:not\(\.focused-pane\):not\(\.typing-ready-pane\)[^{]*\.panel-overlay-root::after/.test(fs.readFileSync('static/yolomux.css', 'utf8')), false, 'inactive-pane dim must still paint on a stale typing-ready pane');
    assert.equal(/updateInactivePaneGradientDirs|has-inactive-pane-gradient|pane-gradient-dir/.test(src), false, 'inactive-pane gradient JS is removed until the feature is revisited');
    // #260: a drag-drop open establishes a clean baseline (clears external-change flags on a fresh,
    // non-dirty open) so it never pops a spurious reload prompt — matching double-click.
    assert.ok(/function openDraggedFilesInEditor[\s\S]*?if \(draggedState && !draggedState\.dirty\) \{[\s\S]*?delete draggedState\.externalChanged/.test(src), '#260: drag-drop open clears externalChanged on a non-dirty fresh open (no spurious reload prompt)');
    // boot() resolves the raw general.language pref (so a system pref localizes client-side).
    assert.ok(/await applyLocale\(resolveLocalePref\(initialSetting\('general\.language', 'system'\)\)\)/.test(src), 'Phase 1: boot resolves the raw language pref (system -> navigator)');
    // The Spanish locale ships with full key-parity and real (non-English) translations.
    const en = JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'));
    const es = JSON.parse(fs.readFileSync('static/locales/es.json', 'utf8'));
    assert.deepEqual(Object.keys(es).sort(), Object.keys(en).sort(), 'Phase 1: es.json has exactly the same keys as en.json (parity)');
    const contextMenuOpenKeys = ['contextmenu.openInDiffer', 'contextmenu.openNewDiffEditor', 'contextmenu.openNewEditor'];
    const updatePreferenceKeys = [
      'pref.general.reload_on_update.label',
      'pref.general.reload_on_update.help',
      'pref.general.reload_on_update_auto.label',
      'pref.general.reload_on_update_auto.help',
      'pref.updates.notify_level.label',
      'pref.updates.notify_level.help',
      'pref.updates.notify_level.major',
      'pref.updates.notify_level.minor',
      'pref.updates.notify_level.patch',
      'pref.updates.notify_level.none',
    ];
    assert.equal(en['contextmenu.openInDiffer'], 'Open in a Differ', 'en reusable Differ context label');
    assert.equal(en['contextmenu.openNewDiffEditor'], 'Open in a new Differ', 'en new Differ context label');
    assert.equal(en['contextmenu.openNewEditor'], 'Open in a new Editor', 'en new Editor context label');
    assert.equal(en['pref.general.reload_on_update.label'], 'Show reload prompt after server/client mismatch', 'en server/client-version reload label is specific');
    assert.equal(en['update.available'], 'The YOLOmux server version changed since this browser tab loaded. Do you want to reload the browser?', 'en server/client reload prompt asks whether to reload the browser');
    assert.equal(en['update.dismiss'], 'Keep', 'en server/client reload prompt has a visible Keep action');
    assert.equal(en['pref.updates.notify_level.label'], 'Notify when change is version (major.minor.patch, like 0.2.345)', 'en update notification threshold label is specific');
    for (const loc of ['es', 'ja', 'de', 'fr', 'pt-BR', 'ru', 'ko', 'hi', 'ar', 'he', 'vi', 'th', 'tr', 'nl', 'pl', 'it', 'zh-Hans', 'zh-Hant']) {
      const cat = JSON.parse(fs.readFileSync(`static/locales/${loc}.json`, 'utf8'));
      for (const key of contextMenuOpenKeys) {
        assert.ok(typeof cat[key] === 'string' && cat[key].length, `${loc} has ${key}`);
        assert.notEqual(cat[key], en[key], `${loc} translates ${key} instead of falling back to English`);
      }
      for (const key of updatePreferenceKeys) {
        assert.ok(typeof cat[key] === 'string' && cat[key].length, `${loc} has ${key}`);
        assert.notEqual(cat[key], en[key], `${loc} localizes ${key} instead of falling back to English`);
      }
      for (const key of ['share.maxTime', 'share.maxViewers', 'share.newShare', 'share.readOnly', 'drop.pathInserted']) {
        assert.ok(typeof cat[key] === 'string' && cat[key].length, `${loc} has ${key}`);
        assert.notEqual(cat[key], en[key], `${loc} localizes ${key} instead of falling back to English`);
      }
      assert.ok(typeof cat['unit.minute.short'] === 'string' && cat['unit.minute.short'].length, `${loc} has unit.minute.short`);
    }
    // The YO!info / YO!agent tab labels are localized via brand.tab.*; en (and non-Chinese locales) keep
    // the English brand text, while the two Chinese catalogs render the requested glyphs (asserted below).
    assert.equal(en['brand.tab.info'], 'YO!info', 'en YO!info tab label');
    assert.equal(en['brand.tab.agent'], 'YO!agent', 'en YO!agent tab label');
    assert.equal(en['menu.tmux.yo.on'], 'YO (auto approve; YOLO)', 'the tmux YO menu identifies YO as auto-approve / YOLO');
    assert.equal(en['menu.tmux.yolo.enableFor'], "Enable YOLO (auto-approve) for Tmux Session '{session}'", 'the overflow YOLO action states its auto-approve behavior');
    assert.ok(/function tmuxCurrentYoloCommand\(session\)[\s\S]*const label = t\('menu\.tmux\.yo\.on'\)/.test(fs.readFileSync('static_src/js/yolomux/30_app_menus.js', 'utf8')), 'the tmux dropdown uses the explicit YO auto-approve label regardless of its current state');
    assert.equal(es['menu.file'], 'Archivo', 'Phase 1: es translates a representative menu label');
    assert.equal(es['pref.reset.cancel'], 'Cancelar', 'Phase 1: es translates the reset cancel button');
    assert.ok(es['pref.appearance.file_explorer_font_size.label'].includes('{name}'), 'Phase 1: es preserves interpolation placeholders');
    const ja = JSON.parse(fs.readFileSync('static/locales/ja.json', 'utf8'));
    assert.deepEqual(Object.keys(ja).sort(), Object.keys(en).sort(), 'Phase 1: ja.json has exactly the same keys as en.json (parity)');
    assert.equal(ja['menu.file'], 'ファイル', 'Phase 1: ja translates a representative menu label');
    assert.equal(ja['pref.reset.cancel'], 'キャンセル', 'Phase 1: ja translates the reset cancel button');
    assert.ok(ja['changes.fileCount.other'].includes('{count}'), 'Phase 1: ja preserves count placeholders');
    const de = JSON.parse(fs.readFileSync('static/locales/de.json', 'utf8'));
    assert.deepEqual(Object.keys(de).sort(), Object.keys(en).sort(), 'Phase 2: de.json has exactly the same keys as en.json (parity)');
    assert.equal(de['menu.file'], 'Datei', 'Phase 2: de translates a representative menu label');
    assert.equal(de['login.signIn'], 'Anmelden', 'Phase 2: de translates the login sign-in label');
    const fr = JSON.parse(fs.readFileSync('static/locales/fr.json', 'utf8'));
    assert.deepEqual(Object.keys(fr).sort(), Object.keys(en).sort(), 'Phase 2: fr.json has exactly the same keys as en.json (parity)');
    assert.equal(fr['menu.file'], 'Fichier', 'Phase 2: fr translates a representative menu label');
    assert.equal(fr['pref.reset.cancel'], 'Annuler', 'Phase 2: fr translates the reset cancel button');
    // The Phase 2 tail locales all ship with full key-parity and preserve placeholders.
    for (const loc of ['pt-BR', 'ru', 'ko', 'hi', 'ar', 'he']) {
      const cat = JSON.parse(fs.readFileSync(`static/locales/${loc}.json`, 'utf8'));
      assert.deepEqual(Object.keys(cat).sort(), Object.keys(en).sort(), `Phase 2: ${loc}.json has exactly the same keys as en.json (parity)`);
      assert.equal(cat['brand.marker'], 'YO', `Phase 2: ${loc} keeps the YO brand marker`);
      assert.equal(cat['brand.tab.info'], 'YO!info', `Phase 2: ${loc} keeps the YO!info tab label`);
      assert.equal(cat['brand.tab.agent'], 'YO!agent', `Phase 2: ${loc} keeps the YO!agent tab label`);
      assert.ok(cat['pref.appearance.file_explorer_font_size.label'].includes('{name}'), `Phase 2: ${loc} preserves the {name} placeholder`);
      assert.ok(cat['yoagent.files'].includes('{count}') && cat['yoagent.files'].includes('{added}'), `Phase 2: ${loc} preserves count/added placeholders`);
      assert.notEqual(cat['menu.file'], 'File', `Phase 2: ${loc} actually translates (menu.file not English)`);
      // Phase 3: the new Intl-wrap + deterministic-framing keys ship in every locale.
      for (const k of ['yoagent.updated.wrap', 'det.noBackend', 'det.noActivity', 'det.openPending']) {
        assert.ok(typeof cat[k] === 'string' && cat[k].length, `Phase 3: ${loc} has ${k}`);
      }
      assert.ok(cat['yoagent.updated.wrap'].includes('{rel}'), `Phase 3: ${loc} preserves the {rel} placeholder`);
    }
    // Phase 4 locales ship in the developer-priority batch and preserve the same catalog contract.
    const phase4Expected = {
      vi: {menuFile: 'Tệp', loginSignIn: 'Đăng nhập', language: 'Ngôn ngữ'},
      th: {menuFile: 'ไฟล์', loginSignIn: 'เข้าสู่ระบบ', language: 'ภาษา'},
      tr: {menuFile: 'Dosya', loginSignIn: 'Oturum aç', language: 'Dil'},
      nl: {menuFile: 'Bestand', loginSignIn: 'Inloggen', language: 'Taal'},
      pl: {menuFile: 'Plik', loginSignIn: 'Zaloguj', language: 'Język'},
      it: {menuFile: 'File', loginSignIn: 'Accedi', language: 'Lingua'},
    };
    for (const [loc, expected] of Object.entries(phase4Expected)) {
      const cat = JSON.parse(fs.readFileSync(`static/locales/${loc}.json`, 'utf8'));
      assert.deepEqual(Object.keys(cat).sort(), Object.keys(en).sort(), `Phase 4: ${loc}.json has exactly the same keys as en.json (parity)`);
      assert.equal(cat['brand.marker'], 'YO', `Phase 4: ${loc} keeps the YO brand marker`);
      assert.equal(cat['menu.file'], expected.menuFile, `Phase 4: ${loc} translates the File menu label`);
      assert.equal(cat['login.signIn'], expected.loginSignIn, `Phase 4: ${loc} translates the login sign-in label`);
      assert.equal(cat['language.switcher'], expected.language, `Phase 4: ${loc} translates the language switcher label`);
      assert.ok(cat['pref.appearance.file_explorer_font_size.label'].includes('{name}'), `Phase 4: ${loc} preserves the {name} placeholder`);
      assert.ok(cat['yoagent.files'].includes('{count}') && cat['yoagent.files'].includes('{added}') && cat['yoagent.files'].includes('{removed}'), `Phase 4: ${loc} preserves count/added/removed placeholders`);
      assert.ok(cat['yoagent.updated.wrap'].includes('{rel}'), `Phase 4: ${loc} preserves the {rel} placeholder`);
      assert.notEqual(cat['yoagent.prompt.answerLanguage'], en['yoagent.prompt.answerLanguage'], `Phase 4: ${loc} sets a localized YO!agent answer-language directive`);
    }
  });

  test('topbar language button survives passive background topbar renders while focused', () => {
    const api = loadYolomux('', ['1']);
    api.renderSessionButtonsForTest({force: true});
    const root = api.sessionButtonsForTest();
    const button = root.querySelector('.topbar-language');
    assert.ok(button, 'language button is rendered');
    api.setDocumentActiveElementForTest(button);
    assert.equal(api.topbarControlIsActiveForTest(), true, 'focused language button is treated as active topbar control');

    api.renderSessionButtonsForTest();

    assert.equal(root.querySelector('.topbar-language'), button, 'passive render preserves the focused language button node');
    assert.equal(api.pendingSessionButtonsRenderForTest(), true, 'passive render records a pending topbar refresh');

    api.setDocumentActiveElementForTest(null);
    api.renderSessionButtonsForTest();

    assert.notEqual(root.querySelector('.topbar-language'), button, 'unfocused passive render can rebuild the topbar');
    assert.equal(api.pendingSessionButtonsRenderForTest(), false, 'unfocused passive render clears pending state');
  });

  test('topbar language blur flushes a deferred topbar render once focus leaves', () => {
    const api = loadYolomux('', ['1']);
    api.renderSessionButtonsForTest({force: true});
    const root = api.sessionButtonsForTest();
    const button = root.querySelector('.topbar-language');
    api.setDocumentActiveElementForTest(button);
    api.renderSessionButtonsForTest();
    assert.equal(root.querySelector('.topbar-language'), button, 'focused button is preserved before blur');
    assert.equal(api.pendingSessionButtonsRenderForTest(), true, 'pending render is queued before blur');

    api.setDocumentActiveElementForTest(null);
    const blurListeners = button.listeners.get('blur') || [];
    assert.ok(blurListeners.length > 0, 'language button has a blur listener');
    blurListeners.forEach(listener => listener({target: button}));

    assert.notEqual(root.querySelector('.topbar-language'), button, 'blur flush replaces the deferred topbar after focus leaves');
    assert.equal(api.pendingSessionButtonsRenderForTest(), false, 'blur flush clears pending state');
  });

  test('t@7555', () => {
    // Phase 3: relative time renders via Intl.RelativeTimeFormat(activeLocale) (native phrasing).
    const api = loadYolomux('', ['1']);
    api.setActiveLocaleForTest('en');
    assert.equal(api.relativeTimeFormat(120), '2 minutes ago', 'Phase 3: en relative time is "2 minutes ago" via Intl');
    assert.equal(api.relativeTimeFormat(7200), '2 hours ago', 'Phase 3: hours via Intl');
    assert.equal(api.relativeTimeFormat(172800), '2 days ago', 'Phase 3: days via Intl');
    assert.equal(api.compactRelativeTimeFormat(180), '3 min ago', 'YO!agent recent-agent chips use compact relative time');
    const src = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/new Intl\.RelativeTimeFormat\(i18nActiveLocale/.test(src), 'Phase 3: relativeTimeFormat uses Intl.RelativeTimeFormat with the active locale');
    assert.ok(/t\('yoagent\.updated\.wrap', \{rel: relativeTimeFormat\(seconds\)\}\)/.test(src), 'Phase 3: the activity "last updated" line wraps the Intl relative time');
  });

  test('t@7567', () => {
    // tab-move latency. The shape signature ignores tabs order / active item, so a reorder or
    // activate is a "same shape" change that takes the cheap in-place branch (no grid/topbar teardown,
    // no server re-poll).
    const api = loadYolomux('', ['1', '2']);
    const slots = api.defaultLayoutSlots();
    const sigA = api.layoutShapeSignature(slots);
    // Mutating a pane's active item / tabs order does NOT change the shape signature.
    const clone = JSON.parse(JSON.stringify(slots));
    for (const key of Object.keys(clone)) {
      if (key !== '__tree' && clone[key] && Array.isArray(clone[key].tabs)) {
        clone[key].tabs = clone[key].tabs.slice().reverse();
        clone[key].active = clone[key].tabs[0];
      }
    }
    assert.equal(api.layoutShapeSignature(clone), sigA, '#reorder/activate keeps the same shape signature');
    // A different tree TOPOLOGY (a split) yields a different signature -> full rebuild path.
    const split = {'__tree': {split: 'row', pct: 50, children: [{slot: 'slot1'}, {slot: 'slot2'}]}, slot1: {tabs: ['1'], active: '1'}, slot2: {tabs: ['2'], active: '2'}};
    assert.notEqual(api.layoutShapeSignature(split), sigA, '#a split changes the shape signature');

    // S1: applyLayoutSlots no longer re-polls the server (refreshTranscripts removed from its body).
    const layoutSrc = fs.readFileSync('static/yolomux.js', 'utf8');
    const applyBody = layoutSrc.slice(layoutSrc.indexOf('function applyLayoutSlots'), layoutSrc.indexOf('function updateActiveSessionParam'));
    assert.equal(/refreshTranscripts\(\);/.test(applyBody), false, '#applyLayoutSlots does not call refreshTranscripts() (no server re-poll on a local layout change)');
    // applyLayoutSlots delegates the shape decision to the shared scheduler.
    const schedulerBody = layoutSrc.slice(layoutSrc.indexOf('function performLayoutRender'), layoutSrc.indexOf('function updateActiveSessionParam'));
    assert.ok(/requestLayoutRender\(\{[\s\S]*?prevShape[\s\S]*?nextShape: layoutShapeSignature\(layoutSlots\)/.test(applyBody), '#applyLayoutSlots sends prev/next shape to the shared layout scheduler');
    assert.ok(/function requestLayoutRender[\s\S]*?pendingLayoutRender = mergePendingLayoutRender/.test(schedulerBody), '#scheduler stores structured deferred render state during drag');
    assert.ok(/layoutRenderCanUseCheap\(renderRequest\)[\s\S]*?syncActivePanelsInPlace\(\)/.test(schedulerBody), '#same-shape changes take the in-place branch');
    assert.ok(/renderSessionButtons\(\);\s*renderPanels\(previousActive/.test(schedulerBody), '#shape changes still fall through to the full rebuild');
    assert.ok(layoutSrc.includes('function syncActivePanelsInPlace'), '#the in-place panel swap exists');
    // fix 6: the markdown preview render is guarded by a path+content signature.
    assert.ok(/container\._previewPath !== path \|\| container\._previewText !== text/.test(layoutSrc), '#fix 6: renderEditorPreviewPane skips re-rendering unchanged markdown');
  });

  test('t@7602', () => {
    // Phase 0: i18n runtime — t()/tPlural() fallback + interpolation, active-over-en, pseudo.
    const api = loadYolomux('', ['1']);
    api.i18nSetCatalogForTest('en', {greet: 'Hi {name}', plain: 'Plain'});
    api.setActiveLocaleForTest('en');
    assert.equal(api.t('greet', {name: 'Al'}), 'Hi Al', 't() interpolates {params}');
    assert.equal(api.t('plain'), 'Plain', 't() returns the catalog value');
    assert.equal(api.t('missing.key'), 'missing.key', 't() falls back to the key when absent (never blank)');
    api.i18nSetCatalogForTest('en', {'files.one': '{count} file', 'files.other': '{count} files'});
    assert.equal(api.tPlural('files', 1), '1 file', 'tPlural picks the one category');
    assert.equal(api.tPlural('files', 3), '3 files', 'tPlural picks the other category');
    api.i18nSetCatalogForTest('en', {x: 'English'});
    api.i18nSetCatalogForTest('zz', {x: 'Zzz'});
    api.setActiveLocaleForTest('zz');
    assert.equal(api.t('x'), 'Zzz', 'active locale wins over the en fallback');
    assert.equal(api.t('y'), 'y', 'missing-in-active falls through en to the key');
  });

  test('t@7620', () => {
    // Phase 0: the Preferences General section + section titles render through t(); under the
    // en-XA pseudo-locale every extracted label is accented/padded, with no plain-English leakage.
    const api = loadYolomux('', ['1']);
    const enXA = JSON.parse(fs.readFileSync('static/locales/en-XA.json', 'utf8'));
    api.i18nSetCatalogForTest('en-XA', enXA);
    api.setActiveLocaleForTest('en-XA');
    const html = api.preferencesPanelHtmlForTest('');
    assert.ok(html.includes(enXA['pref.section.general']), 'pseudo-locale section title renders');
    assert.ok(html.includes(enXA['pref.general.auto_focus.label']), 'pseudo-locale General field label renders');
    assert.ok(html.includes(enXA['pref.general.language.help']), 'pseudo-locale field help renders');
    assert.ok(html.includes(enXA['pref.searchButton']), 'pseudo-locale Preferences search button renders');
    assert.equal(html.includes('Auto-focus active pane'), false, 'no plain-English General field label leaks under the pseudo-locale');
    // Phase 0 (extraction complete): every preference section's fields are i18n-keyed, so the
    // pseudo-locale accents them and NO plain-English label/help from any section leaks through.
    for (const key of [
      'pref.appearance.theme.label', 'pref.appearance.terminal_theme.help',
      'pref.appearance.date_time_hour_cycle.label', 'pref.appearance.font_sizes.note',
      'pref.performance.latency_refresh_ms.label', 'pref.performance.event_log_refresh_ms.label',
      'pref.performance.server_event_poll_ms.label', 'pref.performance.server_background_file_event_poll_ms.label',
      'pref.performance.server_directory_event_poll_ms.label',
      'pref.performance.tabber_activity_refresh_ms.label', 'pref.performance.agent_status_pulse_period_ms.label', 'pref.performance.workflow_transition_glow_seconds.label',
      'pref.editorScheme.group.dark',
      'pref.notifications.throttle_seconds.label',
      'pref.terminal_editor.scrollback.label', 'pref.uploads.max_bytes.label',
      'pref.yoagent.backend.label', 'pref.yoagent.claude_model.label',
      'pref.yoagent.codex_model.label', 'pref.yolo.dry_run.label',
    ]) {
      assert.ok(html.includes(enXA[key]), `pseudo-locale renders ${key}`);
    }
    for (const englishLeak of [
      'Global appearance', 'Editor/Terminal font sizes are in Terminal / Editor.', 'Client pull: latency ping', 'Notification throttle',
      'Terminal scrollback', 'File transfer size cap', 'YO!agent backend', 'Dry run',
    ]) {
      assert.equal(html.includes(englishLeak), false, `no plain-English "${englishLeak}" leaks under the pseudo-locale`);
    }
  });

  test('t@7654', () => {
    // zh-Hant + zh-Hans catalogs localize the WHOLE Preferences panel, and the language select offers
    // both endonym-labeled in product-priority order.
    const api = loadYolomux('', ['1']);
    // The select offers the two Chinese options in their own script, Traditional listed before Simplified.
    const selectHtml = api.preferencesPanelHtmlForTest('language');
    assert.ok(selectHtml.includes('<option value="zh-Hant"'), 'language select offers Traditional Chinese');
    assert.ok(selectHtml.includes('<option value="zh-Hans"'), 'language select offers Simplified Chinese');
    assert.ok(selectHtml.includes('>繁體中文</option>') && selectHtml.includes('>简体中文</option>'), 'Chinese options use endonym labels');
    assert.ok(selectHtml.indexOf('value="zh-Hant"') < selectHtml.indexOf('value="zh-Hans"'), 'Traditional Chinese is listed before Simplified');
    for (const locale of ['zh-Hant', 'zh-Hans']) {
      const catalog = JSON.parse(fs.readFileSync(`static/locales/${locale}.json`, 'utf8'));
      // Same key set as English (the build enforces this; assert it here too).
      assert.deepStrictEqual(new Set(Object.keys(catalog)), new Set(Object.keys(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8')))), `${locale} has the same keys as en`);
      api.i18nSetCatalogForTest(locale, catalog);
      api.setActiveLocaleForTest(locale);
      const zhHtml = api.preferencesPanelHtmlForTest('');
      assert.ok(zhHtml.includes(catalog['pref.appearance.theme.label']), `${locale} renders the localized global-theme label`);
      assert.ok(zhHtml.includes(catalog['pref.appearance.date_time_hour_cycle.label']), `${locale} renders the localized date/time clock label`);
      assert.ok(zhHtml.includes(catalog['pref.appearance.active_color.label']), `${locale} renders the localized Active color label`);
      assert.ok(zhHtml.includes(catalog['pref.appearance.active_color.help']), `${locale} renders the localized Active color help`);
      assert.ok(zhHtml.includes(catalog['pref.appearance.separator_color.label']), `${locale} renders the localized separator color label`);
      assert.ok(zhHtml.includes(catalog['pref.appearance.separator_color.help']), `${locale} renders the localized separator color help`);
      for (const key of ['blue', 'green', 'orange', 'purple', 'white', 'yellow']) {
        assert.ok(zhHtml.includes(catalog[`pref.appearance.active_color.${key}`]), `${locale} renders the localized Active color ${key} choice`);
      }
      assert.ok(zhHtml.includes(catalog['pref.general.startup_tips.label']), `${locale} renders the localized Startup Tips label`);
      assert.ok(zhHtml.includes(catalog['pref.general.startup_tips.help']), `${locale} renders the localized Startup Tips help`);
      assert.ok(zhHtml.includes(catalog['pref.section.yolo']), `${locale} renders the localized YOLO section title`);
      assert.ok(zhHtml.includes(catalog['pref.path.rules']), `${locale} renders the localized YOLO rules path label`);
      assert.ok(zhHtml.includes(catalog['pref.performance.auto_approve_interval_seconds.label']), `${locale} renders the localized YOLO worker poll label`);
      assert.ok(zhHtml.includes(catalog['pref.yolo.rule_file_path.help']), `${locale} renders the localized YOLO rule-file help`);
      assert.ok(zhHtml.includes(catalog['pref.section.yoagent']), `${locale} renders the localized YO!agent section title`);
      // Brand glyph: YO!agent localizes to 優!助手 / 优!助手 (no plain "YO!agent" section title leak).
      assert.ok(catalog['pref.section.yoagent'].includes(locale === 'zh-Hant' ? '優!助手' : '优!助手'), `${locale} applies the YO!agent brand glyph`);
      // The YO marker glyph localizes to 優 / 优 (the catalog value the marker renders via t('brand.marker')).
      assert.equal(catalog['brand.marker'], locale === 'zh-Hant' ? '優' : '优', `${locale} marker glyph`);
      // #52: the wordmark glyphs localize to 優樂 / 优乐.
      assert.equal(catalog['brand.wordmark.yo'], locale === 'zh-Hant' ? '優' : '优', `${locale} wordmark YO glyph`);
      assert.equal(catalog['brand.wordmark.lo'], locale === 'zh-Hant' ? '樂' : '乐', `${locale} wordmark LO glyph`);
      // The user's request: YO!info -> 優!資料 / 优!资料, YO!agent -> 優!助手 / 优!助手.
      assert.equal(catalog['brand.tab.info'], locale === 'zh-Hant' ? '優!資料' : '优!资料', `${locale} YO!info tab label`);
      assert.equal(catalog['brand.tab.agent'], locale === 'zh-Hant' ? '優!助手' : '优!助手', `${locale} YO!agent tab label`);
      const localizedDate = api.sessionFileTimeText(Date.UTC(2026, 5, 4, 19, 17) / 1000);
      assert.equal(localizedDate.includes('Jun'), false, `${locale} Finder date does not leak the English month name`);
      assert.ok(/[年月日]/.test(localizedDate), `${locale} Finder date uses Chinese date wording`);
      assert.equal(/上午|下午|[AP]\.?M\.?/i.test(localizedDate), false, `${locale} Finder date defaults to a 24-hour clock`);
      assert.ok(/\d{2}:\d{2}/.test(localizedDate), `${locale} Finder date includes a two-digit clock`);
      assert.equal(api.fileExplorerTreeDateModeLabel('none'), catalog['finder.dateMode.none'], `${locale} Finder/Differ None date-mode button is localized`);
      assert.equal(api.fileExplorerTreeDateModeButtonLabel('none'), catalog['finder.dateMode.date'], `${locale} Finder/Differ None date-mode button shows localized crossed-out Date`);
      assert.equal(api.fileExplorerTreeDateModeLabel('date'), catalog['finder.dateMode.date'], `${locale} Finder/Differ Date date-mode button is localized`);
      assert.equal(api.fileExplorerTreeDateModeLabel('relative'), catalog['finder.dateMode.relative'], `${locale} Finder/Differ Ago date-mode button is localized`);
      assert.equal(api.fileExplorerTreeDateModeTitle('relative').includes('None'), false, `${locale} Finder/Differ date-mode tooltip does not leak English None`);
      assert.equal(api.fileExplorerTreeDateModeTitle('relative').includes('Date display'), false, `${locale} Finder/Differ date-mode tooltip does not leak English title text`);
      assert.equal(api.sessionFileRelativeTimeText(1000, 1014), catalog['relative.compact.lessThan15Sec'], `${locale} Finder/Differ sub-15-second Ago text is localized`);
      assert.equal(api.sessionFileRelativeTimeText(1000, 19720), catalog['relative.compact.hour.other'].replace('{count}', '5.2'), `${locale} Finder/Differ compact Ago text is localized`);
      assert.equal(/\bago\b|hrs?|days?|min\b/i.test(api.sessionFileRelativeTimeText(1000, 217000)), false, `${locale} Finder/Differ compact Ago text does not leak English units`);
      assert.equal(api.editorModeLabel('edit'), catalog['editor.mode.edit'], `${locale} editor Edit mode label is localized`);
      assert.equal(api.editorModeLabel('preview'), catalog['editor.mode.preview'], `${locale} editor Preview mode label is localized`);
      assert.equal(api.editorModeLabel('split'), catalog['editor.mode.split'], `${locale} editor Split View mode label is localized`);
      assert.notEqual(api.editorModeLabel('edit'), 'Edit', `${locale} editor Edit mode label does not fall back to English`);
      assert.notEqual(api.editorModeLabel('preview'), 'Preview', `${locale} editor Preview mode label does not fall back to English`);
      assert.notEqual(api.editorModeLabel('split'), 'Split view', `${locale} editor Split View mode label does not fall back to English`);
      // The YOLO-toggle menu labels + the YOLO submenu header use the localized brand glyph (優/优 and
      // 優樂/优乐), not a Latin "YO"/"YOLO" (images #57 / #59).
      const glyph = locale === 'zh-Hant' ? '優' : '优';
      for (const k of ['menu.tmux.yo.on', 'menu.tmux.yo.off', 'menu.tmux.yo.elsewhere', 'menu.tmux.yo.none', 'menu.tmux.yoloSubmenu']) {
        assert.equal(/[A-Za-z]/.test(catalog[k]), false, `${locale} ${k} has no Latin "YO" leak`);
        assert.ok(catalog[k].startsWith(glyph), `${locale} ${k} leads with the localized brand glyph`);
      }
      const yoloSectionStart = zhHtml.indexOf(`data-preference-section="${catalog['pref.section.yolo']}"`);
      const yoloSectionEnd = zhHtml.indexOf('data-preference-section="', yoloSectionStart + 1);
      const yoloSectionHtml = zhHtml.slice(yoloSectionStart, yoloSectionEnd >= 0 ? yoloSectionEnd : undefined);
      assert.ok(yoloSectionStart >= 0, `${locale} can isolate the localized YOLO Preferences section`);
      assert.equal(yoloSectionHtml.includes('YOLO'), false, `${locale} YOLO Preferences section does not leak Latin YOLO`);
      api.setClientSettingsPayloadPatchForTest({mtime_ns: 1000000000});
      assert.equal(api.settingsLoadedAgeText(1123), catalog['pref.status.loadedSeconds'].replace('{count}', '0'), `${locale} Preferences loaded age is localized`);
      api.setClientSettingsPayloadPatchForTest({mtime_ns: 0});
      // #54: the System theme option is bilingual (localized + "/System") so the OS-following option is
      // unambiguous in any locale; Dark/Light stay fully localized.
      assert.ok(catalog['pref.appearance.theme.system'].endsWith('/System'), `${locale} System theme option is bilingual`);
      assert.equal(catalog['pref.appearance.theme.dark'].includes('/'), false, `${locale} Dark theme option stays fully localized`);
      for (const englishLeak of [
        'Global appearance',
        'File transfer size cap',
        'Terminal scrollback',
        'Startup Tips',
        'Show one small Tip',
        'Theme color',
        'Deep ocean blue',
        'Envy green',
        'Blood orange',
        'Royal violet',
        'Moon white',
        'Solar gold',
        'YOLO rules',
        'YOLO worker poll interval',
        'Use the supplied',
        'Reply in Markdown',
        'Default shape:',
        'Use the live AI agent activity',
        'You are YO!agent',
        ['autonomous command', 'sending tools'].join('-'),
      ]) {
        assert.equal(zhHtml.includes(englishLeak), false, `${locale}: no plain-English "${englishLeak}" leaks`);
      }
      for (const catalogKey of ['events.title', 'meta.refreshTitle', 'status.selectPaneForImagePaste', 'status.yoloLoading', 'yolo.buttonOnForSession', 'yolo.buttonOffForSession', 'yolo.buttonOwnedBy']) {
        assert.equal(/YOLO/.test(catalog[catalogKey]), false, `${locale}: ${catalogKey} uses the localized YOLO brand`);
      }
    }
  });

  test('t@7714', () => {
    // "Language" is the FIRST General preference and its label is "Language" (not "UI language").
    const api = loadYolomux('', ['1']);
    const enCatalog = JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'));
    assert.equal(enCatalog['pref.general.language.label'], 'Language', '#51: the language label reads "Language"');
    api.setActiveLocaleForTest('en');
    const generalHtml = api.preferencesPanelHtmlForTest('');
    assert.ok(generalHtml.includes('data-setting-path="general.language"'), '#51: the language field is present');
    assert.ok(generalHtml.indexOf('data-setting-path="general.language"') < generalHtml.indexOf('data-setting-path="general.auto_focus"'), '#51: the language field is the first General row (before auto-focus)');
  });

  test('t@7725', () => {
    // startup helper tips are a persisted General preference, rotate serially through
    // localStorage, use the shared toast path, and do not render for readonly users.
    const api = loadYolomux('', ['1']);
    api.setActiveLocaleForTest('en');
    const tips = api.startupHelperCatalog();
    assert.equal(tips.length, 14, 'startup helper catalog includes the initial tip set');
    assert.ok(tips.some(tip => tip.title === 'Drag files into terminals'), 'startup helper catalog includes file drag/drop');
    assert.ok(tips.some(tip => tip.title === 'Ask YO!agent for direction'), 'startup helper catalog includes YO!agent guidance');
    assert.ok(tips.some(tip => tip.title === 'Review agent changes'), 'startup helper catalog includes Differ');
    assert.equal(api.readStartupHelperIndex(tips.length), 0, 'startup helper index defaults to first tip');
    api.writeStartupHelperIndex(15);
    assert.equal(api.readStartupHelperIndex(tips.length), 1, 'startup helper index wraps by catalog length');
    const generalHtml = api.preferencesPanelHtmlForTest('');
    assert.ok(generalHtml.includes('data-setting-path="general.startup_tips"'), 'Startup Tips setting renders in General');
    assert.ok(generalHtml.includes('Startup Tips'), 'Startup Tips setting uses Tips wording');
    assert.ok(generalHtml.indexOf('data-setting-path="general.auto_focus"') < generalHtml.indexOf('data-setting-path="general.startup_tips"'), 'Startup Tips setting follows Auto-focus');
    const src = fs.readFileSync('static_src/js/yolomux/20_layout_state.js', 'utf8');
    assert.ok(src.includes("if (readOnlyMode || !startupHelpersEnabled) return null;"), 'startup helper does not render in readonly or disabled mode');
    assert.ok(src.includes("writeStartupHelperIndex((index + 1) % tips.length)"), 'startup helper advances the localStorage index when shown');
    assert.ok(src.includes("showStartupHelperTip({manual: true})"), 'Next tip action shows the next helper');
    assert.ok(src.includes("saveSettingsPatch(settingPatch('general.startup_tips', false))"), 'Turn off forever persists the General setting');
    assert.ok(src.includes('startupHelperPromptTitle(index, tips.length, tip)'), 'startup helper title includes tip number, total, and action prompt');
    assert.ok(src.includes('container: displayToastContainer(focusedPanelItem)'), 'startup helper renders in the focused pane toast stack, below pane tabs');
    assert.equal(src.includes("startupHelper.action.hide"), false, 'startup helper relies on the toast X instead of a duplicate Hide action');
    assert.ok(src.includes('actions: [navAction, offAction]'), 'startup helper actions are nav plus Turn off Tips forever');
    assert.ok(src.includes("startupHelperAction('<', () => showRelativeTip(-1)"), 'startup helper has a previous-tip arrow control');
    assert.ok(src.includes("startupHelperAction('>', () => showRelativeTip(1)"), 'startup helper has a next-tip arrow control');
    assert.ok(src.includes('countdownMs: 45000'), 'Startup Tips stay visible for 45 seconds');
    const helperStart = src.indexOf('function showStartupHelperTip');
    const helperEnd = src.indexOf('function scheduleStartupHelperTip');
    assert.ok(helperStart >= 0 && helperEnd > helperStart, 'startup helper function block is present');
    assert.equal(src.slice(helperStart, helperEnd).includes('.focus('), false, 'startup helper code does not steal focus');
    const helperCss = fs.readFileSync('static_src/css/yolomux/50_terminal_file_tree.css', 'utf8');
    assert.ok(/\.panel-toast-stack \.startup-helper-toast\s*\{[\s\S]*?align-self:\s*flex-end/.test(helperCss), 'startup helper toast is pane-local and right-aligned below the pane tab strip');
    assert.ok(/\.startup-helper-nav\s*\{[\s\S]*?display:\s*inline-flex/.test(helperCss), 'startup helper navigation is a compact arrow group');
    const bootSrc = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
    assert.ok(/installRuntimeIntervals\(\);\s*scheduleStartupHelperTip\(\);/.test(bootSrc), 'startup helper is scheduled after initial boot intervals');
    assert.ok(src.includes("if (location.protocol === 'file:') return;"), 'startup helper is skipped in file:// browser fixtures');
    const settingsSrc = fs.readFileSync('yolomux_lib/settings.py', 'utf8');
    assert.ok(settingsSrc.includes('"startup_tips": True'), 'Startup Tips setting defaults on server-side');
    assert.ok(settingsSrc.includes('"startup_helpers" in incoming'), 'legacy startup_helpers configs migrate to startup_tips');
  });

  test('t@7769', () => {
    // User screenshot 20260608-004: pane tabs should sit tight to the pane border and to each other.
    const tokenCss = fs.readFileSync('static_src/css/yolomux/00_tokens_base.css', 'utf8');
    const css = fs.readFileSync('static_src/css/yolomux/40_layout_panes_tabs.css', 'utf8');
    const popoverCss = fs.readFileSync('static_src/css/yolomux/20_sessions_popovers.css', 'utf8');
    assert.ok(/\.panel-head\s*\{[\s\S]*?padding:\s*2px 1px 0;/.test(css), 'pane tab strip has a 1px left/right edge gap');
    assert.ok(/\.pane-tab\s*\{[\s\S]*?margin:\s*0 1px 0 0;/.test(css), 'pane tabs have a 1px horizontal gap');
    assert.ok(/\.yolomux-dockview \.dv-tabs-and-actions-container\s*\{[\s\S]*?height:\s*auto;[\s\S]*?overflow:\s*visible;/.test(css), 'Dockview pane headers grow vertically when tabs wrap');
    assert.ok(/\.yolomux-dockview \.dv-tabs-container\s*\{[\s\S]*?flex:\s*1 1 auto;[\s\S]*?flex-wrap:\s*wrap;[\s\S]*?inline-size:\s*100%;[\s\S]*?max-inline-size:\s*100%;[\s\S]*?height:\s*auto;[\s\S]*?max-height:\s*none;[\s\S]*?overflow:\s*visible;/.test(css), 'Dockview tab strips wrap across their full width');
    assert.ok(/\.dockview-tab-first-row-reservation\s*\{[\s\S]*?flex:\s*0 0 var\(--dockview-first-row-reservation-inline-size,\s*0px\)/.test(css), 'Dockview reserves header-action space with a first-row-only flex item');
    assert.equal(css.includes('padding-inline-end: var(--dockview-header-actions-reserved-inline-size, 0px);'), false, 'Dockview does not reserve header-action space on every wrapped row');
    assert.equal(css.includes('.dockview-tab-row-break'), false, 'Dockview tab strips wrap naturally without synthetic row breaks');
    assert.equal(/\.yolomux-dockview \.dv-tabs-container\s*\{[\s\S]*?flex-wrap:\s*nowrap/.test(css), false, 'Dockview pane tabs must not force a one-row nowrap strip');
    assert.ok(/\.yolomux-dockview \.dv-tab\s*\{[\s\S]*?flex:\s*0 0 var\(--dockview-tab-inline-size,\s*var\(--pane-tab-width\)\)/.test(css), 'Dockview pane tabs use the configured preference width by default');
    assert.ok(/\.yolomux-dockview \.dv-tab > \.dockview-pane-tab\s*\{[\s\S]*?border-radius:\s*var\(--pane-tab-top-radius\) var\(--pane-tab-top-radius\) 0 0/.test(css), 'Dockview active tabs round their top corners via the shared pane-tab top-radius token');
    assert.ok(/\.yolomux-dockview \.dv-groupview\s*\{[\s\S]*?border:\s*0;/.test(css), 'Dockview groups do not add a fat pane-spacing border around the skinny sash separator');
    assert.ok(/\.yolomux-dockview \.dv-groupview\s*\{[\s\S]*?padding:\s*var\(--pane-split-gap\);/.test(css), 'Dockview groups reserve pane-spacing width inside the active ring so terminals do not render under it');
    assert.ok(/\.yolomux-dockview \.dv-groupview::after\s*\{[\s\S]*?border:\s*var\(--pane-split-gap\) solid color-mix\(in srgb, var\(--panel-ring-color\) var\(--panel-ring-opacity\), transparent\)/.test(css), 'Dockview groups draw the active surround as a pane-spacing-width pseudo-ring without thickening the sash');
    assert.ok(/\.yolomux-dockview \.dv-groupview:has\(\.file-explorer-panel\)\s*\{[\s\S]*?min-width:\s*var\(--file-pane-min-inline-size\)/.test(css), 'Dockview gives the docked Finder/Differ group a real min-width floor');
    assert.ok(/\.yolomux-dockview \.dv-groupview:has\(\.panel\.active-pane\),[\s\S]*?\.dv-groupview:has\(\.panel\.typing-ready-pane\)\s*\{[\s\S]*?--panel-ring-color:\s*var\(--pane-tab-panel-ring\)/.test(css), 'Dockview active/typing panes feed the same active ring color into the group pseudo-ring');
    assert.ok(/\.yolomux-dockview \.dockview-panel-content > \.panel\s*\{[\s\S]*?border-width:\s*0;/.test(css), 'Dockview-mounted panes do not keep the legacy pane-spacing border');
    assert.ok(/\.yolomux-dockview \.dockview-panel-content > \.panel\.dockview-inner-head-collapsed\s*\{[\s\S]*?grid-template-rows:\s*auto minmax\(0,\s*1fr\)/.test(css), 'Dockview-mounted panes switch from header/detail/content rows to detail/content rows when the inner header is hidden');
    assert.ok(/\.yolomux-dockview \.dockview-panel-content > \.panel > \.panel-head\.dockview-inner-head-hidden,[\s\S]*?\.panel-head\[hidden\]\s*\{[\s\S]*?display:\s*none;/.test(css), 'Dockview hidden inner pane headers really stop rendering instead of leaving a green band');
    assert.ok(/\.yolomux-dockview \.dv-tab\.dv-inactive-tab > \.dockview-pane-tab:not\(\.active\)\s*\{[\s\S]*?background:\s*var\(--pane-bar-bg,\s*var\(--panel2\)\)/.test(css), 'Dockview inactive tabs match the pane tab-strip background');
    assert.ok(/\.yolomux-dockview \.dockview-pane-header-actions \.pane-drag-handle\s*\{[\s\S]*?cursor:\s*grab/.test(css), 'Dockview exposes a compact whole-pane drag handle in the header actions');
    assert.ok(/\.yolomux-dockview \.dockview-pane-header-actions \.tab\s*\{[\s\S]*?height:\s*min\(18px,\s*var\(--pane-tab-height\)\)/.test(css), 'Dockview header action buttons stay compact instead of growing taller than the tab row');
    assert.ok(/\.yolomux-dockview \.dv-split-view-container > \.dv-sash-container > \.dv-sash,[\s\S]*?\.dv-sash:not\(\.disabled\):hover,[\s\S]*?\.dv-sash:not\(\.disabled\):active\s*\{[\s\S]*?background-color:\s*transparent;/.test(css), 'Dockview sash hit targets stay transparent so only the skinny pseudo-line is visible');
    assert.ok(/\.yolomux-dockview \.dv-sash::before\s*\{[\s\S]*?background:\s*var\(--pane-resizer-bg\)/.test(css), 'Dockview sashes draw the shared skinny pane separator at rest');
    assert.ok(/\.yolomux-dockview \.dv-split-view-container\.dv-horizontal > \.dv-sash-container > \.dv-sash::before\s*\{[\s\S]*?left:\s*calc\(50% - \(var\(--pane-resizer-line-size\) \/ 2\)\)/.test(css), 'Dockview horizontal sashes center the 1px resting separator');
    assert.ok(/\.yolomux-dockview \.dv-split-view-container\.dv-horizontal > \.dv-sash-container > \.dv-sash:hover::before,[\s\S]*?var\(--pane-resizer-hover-line-size\)/.test(css), 'Dockview horizontal sashes thicken only to the shared hover separator size');
    assert.ok(css.includes('--dv-drag-over-border: 2px dashed var(--pane-resizer-hover-bg)'), 'Dockview drag overlays use the configurable pane separator color');
    assert.ok(tokenCss.includes('--tab-insert-preview-width: 24px'), 'tab insertion previews use a large enough between-tabs box to see while dragging');
    assert.ok(/\.grid\.drop-preview::before\s*\{[\s\S]*?border:\s*2px dashed var\(--pane-resizer-hover-bg\)/.test(css), 'root tab-drag previews use the configurable pane separator color');
    assert.ok(/\.yolomux-dockview \.dv-groupview\.drop-preview::before/.test(css), 'Dockview panes can draw the shared dashed file/tab drop preview');
    assert.ok(/\.pane-tabs\.tab-drop-preview::after\s*\{[\s\S]*?width:\s*var\(--tab-insert-preview-width\);[\s\S]*?border:\s*2px dashed var\(--pane-resizer-hover-bg\)/.test(css), 'legacy tab insertion previews render as a visible dashed between-tabs box');
    assert.ok(/\.yolomux-dockview \.dv-tab\.dv-drop-target \.dv-drop-target-selection\.dv-drop-target-left,[\s\S]*?\.dv-drop-target-selection\.dv-drop-target-right\s*\{[\s\S]*?width:\s*var\(--tab-insert-preview-width\) !important;[\s\S]*?border:\s*2px dashed var\(--pane-resizer-hover-bg\) !important/.test(css), 'Dockview tab insertion previews render as a visible dashed between-tabs box instead of a half-tab overlay');
    assert.ok(/\.transparent-drag-image\s*\{[\s\S]*?position:\s*fixed;[\s\S]*?left:\s*-10000px;[\s\S]*?top:\s*-10000px;[\s\S]*?width:\s*1px;[\s\S]*?height:\s*1px;[\s\S]*?opacity:\s*0;[\s\S]*?pointer-events:\s*none;/.test(css), 'transparent native drag image appearance is owned by CSS');
    assert.ok(/\.pane-drag-image\.drag-image\s*\{[\s\S]*?border:\s*2px dotted var\(--pane-resizer-hover-bg\)/.test(popoverCss), 'whole-pane drag preview renders as a dotted box using the shared separator color');
    assert.ok(/\.yolomux-dockview \.dv-tab\.dv-drop-target \.dv-drop-target-selection\.dv-drop-target-right\s*\{[\s\S]*?left:\s*100% !important;[\s\S]*?translateX\(-50%\)/.test(css), 'Dockview right-side tab insertion marker is centered on the target tab edge');
    const dockviewSrc = fs.readFileSync('static_src/js/yolomux/75_dockview_layout.js', 'utf8');
    assert.ok(/function dockviewRootBoundaryDropIntent\(event\)[\s\S]*rootBoundaryDropZoneForEvent\(nativeEvent, rect\)[\s\S]*splitSessionAtLayoutBoundary\(rootIntent\.item, rootIntent\.zone, rootIntent\.sourceSlot\)/.test(dockviewSrc), 'Dockview content-edge drops in the root band use the legacy full-span boundary split');
    assert.ok(/function dockviewRootBoundaryDropIntent\(event\)[\s\S]*event\?\.kind !== 'content' && event\?\.kind !== 'edge'/.test(dockviewSrc), 'Dockview edge overlays in the app root band use the bounded YOLOmux root preview instead of the native full-width overlay');
    assert.ok(/function dockviewRootBoundaryDropIntent\(event\)[\s\S]*event\.kind === 'content' && event\.group && !dockviewContentDropCanUseRootBoundary\(nativeEvent, zone\)[\s\S]*return null/.test(dockviewSrc), 'Dockview pane-content drops keep the native local group split unless the pointer is on a root-edge cross-gutter');
    assert.ok(/function dockviewContentDropCanUseRootBoundary\(event, zone\)[\s\S]*const crossSplit = zone === 'left' \|\| zone === 'right' \? 'column' : 'row'[\s\S]*Math\.abs\(pointer - boundary\) <= tolerance/.test(dockviewSrc), 'Dockview root-boundary content drops are limited to the cross-gutter between existing panes');
    assert.ok(/function dockviewContentDropCanUseRootBoundary\(event, zone\)[\s\S]*const tolerance = Math\.max\(48, layoutBoundaryDropBandPx/.test(dockviewSrc), 'Dockview outer-edge drops beside stacked panes use a usable cross-gutter tolerance without stealing normal pane-edge drops');
    assert.ok(/function dockviewPaneContentDropInfo\(event\)[\s\S]*targetSlot[\s\S]*targetRect: layoutSlotScreenRect\(targetSlot\)[\s\S]*function dockviewPaneContentDropIntent\(event\)[\s\S]*dropIntentAllowsSession\(info\.item, info\.intent\)/.test(dockviewSrc), 'Dockview pane-content edge drops are converted to YOLOmux local pane split intents with real target geometry');
    assert.ok(/function dockviewShouldSuppressPaneContentDrop\(event\)[\s\S]*!dropIntentAllowsSession\(info\.item, info\.intent\)[\s\S]*function dockviewTrackRootBoundaryOverlay\(event\)[\s\S]*dockviewShouldSuppressPaneContentDrop\(event\)[\s\S]*event\.preventDefault\?\.\(\)/.test(dockviewSrc), 'Dockview suppresses native previews for invalid pane drops before a dashed box is advertised');
    assert.ok(/api\.onWillDrop\(event => \{[\s\S]*const rootIntent = dockviewRootBoundaryDropIntent\(event\)[\s\S]*const paneIntent = dockviewPaneContentDropIntent\(event\)[\s\S]*splitSessionAtSlot\(paneIntent\.item, paneIntent\.targetSlot, paneIntent\.zone, paneIntent\.sourceSlot\)/.test(dockviewSrc), 'Dockview pane edge drops use splitSessionAtSlot so same-axis splits preserve 1/2 + 1/4 + 1/4 sizing');
    assert.ok(/function dockviewRootBoundaryDropIntent\(event\)[\s\S]*rootBoundaryDropOverDockedFileExplorer\(nativeEvent, zone\)[\s\S]*return null/.test(dockviewSrc), 'Dockview root top/bottom previews defer when the pointer is inside the docked Finder/Differ column');
    assert.ok(/function dockviewPinnedTabCrossPaneViolation\(info\)[\s\S]*info\.createsPane === true[\s\S]*info\.targetSlot && info\.targetSlot !== info\.sourceSlot/.test(dockviewSrc), 'Dockview has one shared pinned-tab rule for cross-pane and new-pane violations');
    assert.ok(/function dockviewTabDropViolatesPinnedPartition\(event\)[\s\S]*dockviewPinnedTabCrossPaneViolation\(info\)[\s\S]*return true/.test(dockviewSrc), 'Dockview tab-strip drops reject pinned tabs that leave their current pane');
    assert.ok(/function dockviewPaneContentDropInfo\(event\)[\s\S]*createsPane: layoutSplitZone\(zone\)[\s\S]*function dockviewPaneContentDropIntent\(event\)[\s\S]*dockviewPinnedTabCrossPaneViolation\(info\.intent\)[\s\S]*return null/.test(dockviewSrc), 'Dockview pane-content drops reject pinned tabs that would split into a new pane');
    assert.ok(/function dockviewTrackRootBoundaryOverlay\(event\)[\s\S]*dockviewPinnedTabRootBoundaryViolation\(intent\)[\s\S]*event\.preventDefault\?\.\(\)/.test(dockviewSrc), 'Dockview root-boundary previews are suppressed for pinned tabs');
    assert.ok(/api\.onWillDrop\(event => \{[\s\S]*const rootIntent = dockviewRootBoundaryDropIntent\(event\)[\s\S]*dockviewPinnedTabRootBoundaryViolation\(rootIntent\)[\s\S]*event\.preventDefault\(\)/.test(dockviewSrc), 'Dockview root-boundary drops do not split pinned tabs into new panes');
    assert.equal(dockviewSrc.includes('dockviewPinnedCrossPane'), false, 'old pinned cross-pane move exception helpers stay removed');
    assert.equal(dockviewSrc.includes('pinnedCrossPanePointerDrop'), false, 'old pinned cross-pane pointer fallback state stays removed');
    assert.ok(/function dockviewTrackRootBoundaryOverlay\(event\)[\s\S]*dockviewShowRootBoundaryPreview\(intent\)[\s\S]*event\.preventDefault\?\.\(\)/.test(dockviewSrc), 'Dockview root-band drags show the bounded YOLOmux preview and suppress the native full-width Dockview overlay');
    assert.ok(dockviewSrc.includes('createRightHeaderActionComponent: () => createDockviewHeaderActionsRenderer()'), 'Dockview renders YOLOmux pane controls in the Dockview header row');
    assert.ok(/function dockviewLayoutToHost[\s\S]*api\.layout\?\.\(width, height\)/.test(dockviewSrc), 'Dockview is explicitly laid out to the host size instead of staying at the default 100px shell');
    assert.ok(dockviewSrc.includes('const DOCKVIEW_MIN_LAYOUT_WIDTH = 640') && dockviewSrc.includes('const DOCKVIEW_MIN_LAYOUT_HEIGHT = 240'), 'Dockview serialized fallback dimensions use functional minimums');
    assert.ok(/function dockviewHostCanAdoptLayout\(host = dockviewLayoutState\.host\)[\s\S]*return width > 1 && height > 1/.test(dockviewSrc), 'Dockview adoption rejects hidden or zero-area hosts');
    assert.ok(/function adoptDockviewLayout\(\)[\s\S]*if \(!dockviewHostCanAdoptLayout\(\)\) return/.test(dockviewSrc), 'Dockview skips adopting snapshots while the host has no measurable area');
    assert.ok(/api\.onDidRemoveGroup\?\.\(group => dockviewHandleRemovedGroup\(group\)\)/.test(dockviewSrc), 'Dockview removed-group events queue Finder/Differ recovery');
    assert.ok(/function dockviewJsonFromLayoutSlots\(slots = layoutSlots\)[\s\S]*Math\.max\(DOCKVIEW_MIN_LAYOUT_HEIGHT[\s\S]*Math\.max\(DOCKVIEW_MIN_LAYOUT_WIDTH/.test(dockviewSrc), 'Dockview JSON snapshots clamp serialized dimensions to functional minimums');
    assert.ok(/function hideDockviewInnerPaneTabs\(panel\)[\s\S]*panel\.classList\.add\('dockview-inner-head-collapsed'\)/.test(dockviewSrc), 'Dockview marks panels whose inner header was hidden so their content row still fills the pane');
    assert.ok(/function preserveDockviewDockedFileExplorerSplit[\s\S]*dockviewLayoutState\.reloadAfterAdoption = true/.test(dockviewSrc), 'Dockview adoption preserves and reapplies the docked Finder root split width');
    assert.ok(/function dockviewInstallFileDropBridge[\s\S]*dockviewHandleFileDragOver[\s\S]*dockviewHandleFileDrop/.test(dockviewSrc), 'Dockview panes bridge Finder/Differ file drags into the shared pane drop behavior');
    assert.ok(/function dockviewHandleFileDrop[\s\S]*openDraggedFilesInEditor\(payload, \{targetSlot: intent\.targetSlot, targetZone: intent\.zone\}\)/.test(dockviewSrc), 'Dockview file drops open dragged files in the intended pane split');
    assert.ok(/function dockviewHandleFileDragOver\(event\)[\s\S]*paneDragPayload\(event\)[\s\S]*paneSwapIntentForEvent\(event, panePayload\.slot\)[\s\S]*showDropPreview\(intent\)/.test(dockviewSrc), 'Dockview host dragover handles whole-pane swap previews separately from tab drags');
    assert.ok(/function dockviewHandleFileDrop\(event\)[\s\S]*paneDragPayload\(event\)[\s\S]*swapPaneSlots\(intent\.sourceSlot, intent\.targetSlot\)/.test(dockviewSrc), 'Dockview host drops swap whole panes when the pane payload is accepted');
    assert.ok(/function paneDragHandleHtml\(item\)[\s\S]*data-pane-drag=/.test(dockviewSrc), 'Dockview header actions include a dedicated pane-drag payload handle');
    assert.ok(/function dockviewSyncHeaderBackgroundDragSources\(\)[\s\S]*\.dv-tabs-and-actions-container[\s\S]*pane-drag-source[\s\S]*dockviewBeginPanePointerDrag\(event, sourceSlot\)/.test(dockviewSrc), 'Dockview tab-container background starts whole-pane drags without marking the tab container draggable');
    assert.ok(/function dockviewSyncHeaderBackgroundDragSources\(\)[\s\S]*\.pane-info-bar[\s\S]*\.panel-detail-row[\s\S]*syncDragSource\(infoBar\)/.test(dockviewSrc), 'Dockview pane Info Bars start the same whole-pane pointer drag as the tab-container background');
    assert.ok(/function dockviewSyncHeaderBackgroundDragSources\(\)[\s\S]*\.file-editor-toolbar[\s\S]*syncDragSource\(editorToolbar\)/.test(dockviewSrc), 'Dockview editor toolbars start the same whole-pane pointer drag as other pane info bars');
    assert.equal(dockviewSrc.includes('dockviewClearTabRowBreaks'), false, 'Dockview does not manage synthetic first-row break nodes');
    assert.ok(/function dockviewSyncHeaderActionReservations\(\)[\s\S]*preferredTabWidth[\s\S]*--pane-tab-width[\s\S]*availableWidth[\s\S]*--dockview-header-actions-reserved-inline-size[\s\S]*--dockview-tab-inline-size/.test(dockviewSrc), 'Dockview measures right-side actions while keeping a consistent configured tab width');
    assert.equal(dockviewSrc.includes('firstRowCapacity'), false, 'Dockview leaves every flex row to the same natural wrapping rules');
    assert.equal(dockviewSrc.includes('fitWidth'), false, 'Dockview tab fitting must not divide the pane width by tab count');
    assert.ok(/function dockviewTrackPanePointerDrag\(event\)[\s\S]*startPaneDragPreview\(event, state\.sourceSlot\)[\s\S]*moveCustomDragPreview\(event\)/.test(dockviewSrc), 'Dockview pane-background pointer drags show and move the same pane drag preview as native pane drags');
    assert.ok(/function dockviewFinishPanePointerDrag\(event\)[\s\S]*stopCustomDragPreview\(\)[\s\S]*clearDropPreview\(\)/.test(dockviewSrc), 'Dockview pane-background pointer drags remove the pane preview on drop/cancel');
    const dragPreviewSrc = fs.readFileSync('static_src/js/yolomux/60_popovers_tabs.js', 'utf8');
    assert.equal(/function transparentNativeDragImage\(\)[\s\S]*node\.style\.(?:position|left|top|width|height|opacity|pointerEvents)\s*=/.test(dragPreviewSrc), false, 'transparent native drag image no longer duplicates static CSS inline');
    assert.ok(/function transparentNativeDragImage\(\)[\s\S]*node\.className = 'transparent-drag-image';[\s\S]*document\.body\.appendChild\(node\);/.test(dragPreviewSrc), 'transparent native drag image still installs the CSS-owned class');
    assert.ok(/const customDragPreviewCleanupEvents = \['drop', 'dragend', 'pointerup', 'mouseup', 'blur', 'visibilitychange'\]/.test(dragPreviewSrc), 'native custom drag previews clean up on drag release and page-cancel paths');
    assert.ok(/function bindCustomDragPreviewListeners\(\)[\s\S]*for \(const target of customDragPreviewEventTargets\(\)\)[\s\S]*target\.addEventListener\?\.\('dragover', moveCustomDragPreview, true\)[\s\S]*target\.addEventListener\?\.\(eventName, stopCustomDragPreview, true\)/.test(dragPreviewSrc), 'native custom drag preview cleanup is bound on both document and window');
    assert.equal(/header\.draggable = draggable/.test(dockviewSrc), false, 'Dockview tab-container background must not become a native draggable ancestor that steals tab drags');
    assert.ok(/api\.onWillDrop\(event => \{[\s\S]*const edgeReorder = dockviewTabEdgeReorderIntent\(event\)[\s\S]*moveSessionToSlot\(edgeReorder\.item, edgeReorder\.targetSlot, edgeReorder\.sourceSlot, edgeReorder\.insertIndex\)/.test(dockviewSrc), 'Dockview manually reorders edge tabs dragged onto their adjacent neighbor');
    assert.ok(/function dockviewInstallTabPointerReorderFallback\(\)[\s\S]*document\.addEventListener\('pointerup', finish, true\)[\s\S]*document\.addEventListener\('mouseup', finish, true\)/.test(dockviewSrc), 'Dockview edge-tab reorder fallback listens to both pointer and mouse release paths');
    assert.ok(/function dockviewFinishTabPointerDrag\(event\)[\s\S]*dockviewTabForPoint[\s\S]*dockviewAdjacentEdgeTabInsertIndex[\s\S]*moveSessionToSlot\(state\.item, targetSlot, targetSlot, currentInsertIndex\)/.test(dockviewSrc), 'Dockview edge-tab pointer fallback reorders against the tab under the release point');
    assert.ok(/\.session-popover-host > \.session-popover,\s*\.pane-tab-detached-popover\s*\{[\s\S]*position:\s*fixed/.test(css), 'Dockview and Tabber tab hover popovers use the shared fixed-position tab popover surface');
    assert.ok(fs.readFileSync('static_src/js/yolomux/78_panel_shell.js', 'utf8').includes('pane-tab session-popover-host'), 'normal pane tabs opt into the shared popover host class');
    assert.ok(/body\.share-replay-shell \.share-mirror-stage \.app-overlay-root,[\s\S]*body\.share-replay-shell \.share-mirror-stage \.pane-tab-detached-popover\s*\{[\s\S]*position:\s*absolute/.test(css), 'YO!share replay positions detached tab popovers inside the transformed app root instead of the viewer viewport');
    assert.ok(/function bindPaneTabPopover\(tab, session\)[\s\S]*tab\.classList\?\.contains\('dockview-pane-tab'\)[\s\S]*detachPaneTabPopover\(tab, popover\)/.test(fs.readFileSync('static_src/js/yolomux/78_panel_shell.js', 'utf8')), 'Dockview tab hover popovers detach from the clipped Dockview tab scroller');
    assert.ok(/function preserveDockviewDockedFileExplorerSplit\(next, previous = layoutSlots\)[\s\S]*dockviewLayoutContentSignature\(next\) === dockviewLayoutContentSignature\(previous\)[\s\S]*return/.test(dockviewSrc), 'Dockview lets sash-only Finder/Differ resize updates change the root split pct');
    assert.ok(/function preserveDockviewContentSplitPercentagesAfterDockResize\(nextRoot, previousRoot, nextDocked, previousDocked\)[\s\S]*copyLayoutSplitPercentagesByShape\(nextContent, previousContent\)[\s\S]*reloadAfterAdoption = true/.test(dockviewSrc), 'Dockview Finder/Differ sash resize preserves nested content split percentages while the root pct changes');
    assert.ok(/function copyLayoutSplitPercentagesByShape\(target, source\)[\s\S]*target\.pct = sourcePct[\s\S]*copyLayoutSplitPercentagesByShape\(targetChildren\[index\], sourceChildren\[index\]\)/.test(dockviewSrc), 'Dockview content pct preservation recurses through matching nested split shapes');
    assert.ok(/function dockviewLayoutContentSignature\(slots = layoutSlots\)[\s\S]*nodeSignature[\s\S]*paneSignature/.test(dockviewSrc), 'Dockview compares content/topology separately from split percentages before preserving Finder width');
    const layoutActionSrc = fs.readFileSync('static_src/js/yolomux/70_layout_actions.js', 'utf8');
    assert.ok(/function layoutNodeScreenRect\(layoutNode\)[\s\S]*\.map\(slot => layoutSlotScreenRect\(slot\)\)/.test(layoutActionSrc), 'Docked Finder preview geometry uses slot screen rects that work for Dockview groups');
    assert.ok(/function layoutSlotScreenRect\(slot\)[\s\S]*\.dockview-panel-content > \.panel\[data-slot=/.test(layoutActionSrc), 'Dockview layout slots can resolve their visible group rectangle');
    assert.ok(/function rootBoundaryDropIntentForEvent\(event\)[\s\S]*rootBoundaryDropOverDockedFileExplorer\(event, zone\)[\s\S]*return null/.test(layoutActionSrc), 'legacy root top/bottom previews also defer inside a docked Finder/Differ column');
    assert.ok(/function fileDropIntentAllowsPayload\(payload, intent\)[\s\S]*dropIntentAllowsSession\(item, intent, \{allowCandidate: true\}\)/.test(layoutActionSrc), 'file drag previews use the same pane/Finder/min-size validator as tab drags');
    assert.ok(/function itemCanSplitSinglePurposePane\(item, intent\)[\s\S]*zone !== 'bottom'[\s\S]*return false[\s\S]*dropIntentHasRoomForItem\(item, intent\)/.test(layoutActionSrc), 'Finder/Differ target panes accept only bottom splits and only when the resulting pane can fit');
    assert.ok(/function dropIntentHasRoomForItem\(item, intent\)[\s\S]*minWidthForLayoutItem\(targetItem\)[\s\S]*targetMinWidth \+ itemMinWidth[\s\S]*targetMinHeight \+ itemMinHeight/.test(layoutActionSrc), 'pane drop previews are suppressed when the target is too small for both resulting panes');
  });

  test('t@7847', () => {
    // Pop-out previews must derive readable light-editor text inside their own document; copied inline
    // aliases like --text/--editor-scheme-fg override the pop-out's editor-theme-light remap.
    const source = [
      'static_src/js/yolomux/90_changes_editor.js',
      'static_src/js/yolomux/93_markdown_preview.js',
      'static_src/js/yolomux/94_preview_renderers.js',
      'static_src/js/yolomux/96_pane_popout.js',
      'static_src/js/yolomux/94_preview_popout.js',
      'static_src/js/yolomux/95_codemirror_editor.js',
    ].map(file => fs.readFileSync(file, 'utf8')).join('');
    const start = source.indexOf('function previewPopoutVariableStyle()');
    const end = source.indexOf('function previewPopoutToolbarHtml()');
    assert.ok(start >= 0 && end > start, 'previewPopoutVariableStyle exists');
    const variableBlock = source.slice(start, end);
    assert.equal(variableBlock.includes("'--text'"), false, 'preview pop-out does not copy --text inline');
    assert.ok(variableBlock.includes("['--editor-scheme-fg', '--popout-editor-scheme-fg']"), 'preview pop-out aliases active editor text instead of copying it onto --text');
    assert.ok(variableBlock.includes("'--code-keyword'") && variableBlock.includes("'--code-control'") && variableBlock.includes("'--code-string'"), 'preview pop-out copies syntax token variables for highlighted fenced code');
    assert.ok(source.includes('.file-preview-popout-window.editor-theme-light .markdown-body pre'), 'preview pop-out has light-theme code block rules outside .file-editor-content');
    assert.ok(source.includes('.file-preview-popout-window .markdown-body'), 'preview pop-out sets readable body text in its standalone document');
    assert.ok(/\.file-preview-popout-title\s*\{[\s\S]*display:\s*grid[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\) auto minmax\(0,\s*1fr\)/.test(source), 'preview pop-out top bar uses left/title, centered font, and right theme zones');
    assert.ok(/\.file-preview-popout-title\s*\{[\s\S]*position:\s*fixed[\s\S]*z-index:\s*1000/.test(source), 'preview pop-out top bar stays fixed above the preview body while scrolling');
    assert.ok(/\.file-preview-popout-shell\s*\{[\s\S]*width:\s*100%[\s\S]*padding:\s*64px 24px 36px/.test(source), 'preview pop-out shell uses the full window width and reserves space below the fixed top bar');
    assert.equal(/\.file-preview-popout-shell\s*\{[\s\S]*width:\s*min\(/.test(source), false, 'preview pop-out content is not capped at a fixed desktop width');
    assert.ok(/<span class="file-preview-popout-title-path">[\s\S]*\$\{previewPopoutToolbarHtml\(\)\}/.test(source), 'preview pop-out header renders the path before the shared pop-out toolbar controls');
    assert.ok(/function previewPopoutToolbarHtml\(\)[\s\S]*file-editor-preview-font-panel[\s\S]*class="file-editor-theme-panel" data-preview-popout-theme/.test(source), 'preview pop-out toolbar renders font selector before the theme selector');
    assert.ok(/updateEditorThemeButton\(themeButton, \{includeVanilla: true\}\)/.test(source), 'preview pop-out theme selector includes vanilla mode');
    assert.ok(/cycleEditorThemeMode\(\{includeVanilla: true\}\)/.test(source), 'preview pop-out theme click cycles dark/light/vanilla');
    assert.ok(source.includes('min-width: 66px;') && source.includes('width: auto;'), 'preview pop-out theme selector leaves room for the visible Dark/Bright/Vanilla label');
    assert.ok(fs.readFileSync('static_src/css/yolomux/60_editor_file_panels.css', 'utf8').includes('.file-editor-theme-panel.theme-with-label::after'), 'preview theme selector renders the current mode label instead of hiding vanilla in the tooltip');
    assert.ok(source.includes('applyMarkdownFenceFallbackHighlight(block);'), 'markdown fenced code falls back to editor syntax highlighting when hljs lacks the language');
    assert.ok(source.includes("const filePreviewPopouts = panePopoutNamespaceMap('file-preview')"), 'preview pop-out uses the shared pane-popout registry namespace');
    assert.ok(/function writePanePopoutDocument\(popoutWindow, options = \{\}\)[\s\S]*currentStylesheetHref\('yolomux\.css'\)[\s\S]*doc\.write/.test(source), 'generic pane pop-outs share one same-origin document writer with copied stylesheet/theme variables');
    assert.ok(/function panePopoutVariableStyle\(\)[\s\S]*name\?\.startsWith\?\.\('--'\)[\s\S]*root\.getPropertyValue\(name\)/.test(source), 'generic pane pop-outs copy current CSS variables instead of hard-coding per-pane theme values');
    assert.ok(/function openPanePopout\(item\)[\s\S]*tabTypeForItem\(item\)[\s\S]*window\.open\(`\/pane-popout\?item=\$\{encodeURIComponent\(item\)\}`/.test(source), 'generic pane pop-out opens a same-origin detached pane shell');
    assert.ok(/function panePopoutDisabledReason\(item\)[\s\S]*isTmuxSession\(item\)[\s\S]*live terminal\/transcript popout is disabled in phase 1[\s\S]*popoutDisabledReason/.test(source), 'unsupported live or interactive pane pop-outs carry an explicit phase-1 disabled reason');
    assert.ok(source.includes("window.open(`/preview-popout?path=${encodeURIComponent(path)}`"), 'preview pop-out opens a same-origin URL instead of about:blank');
    assert.ok(/'editor-popout-preview': \(\) => \{[\s\S]*if \(openFilePreviewPopout\(path, panel\)\) \{[\s\S]*setFileEditorViewMode\(path, 'edit', item\);[\s\S]*renderFileEditorPanel\(panel, item\);/.test(source), 'pressing Pop-out opens the preview window and returns the in-pane editor to Edit mode');
    assert.ok(/function openFilePreviewPopout\(path, panel = null\)[\s\S]*return true;[\s\S]*return false;/.test(source), 'preview pop-out open path reports whether a pop-out was actually opened or focused');
    assert.ok(/function bumpFilePreviewPopoutGeneration\(path\)[\s\S]*record\.previewGeneration[\s\S]*function filePreviewPopoutGenerationMatches\(path, previewWindow, generation\)[\s\S]*record\.window === previewWindow && record\.previewGeneration === generation/.test(source), 'async preview pop-out snapshots are generation-guarded so stale Mermaid renders cannot overwrite newer content');
    assert.ok(/function writeFilePreviewPopoutWhenReady\(path, previewWindow, text\)[\s\S]*renderedPreviewSnapshot\(path, text\)[\s\S]*renderedPreviewSnapshotAsync\(path, text\)[\s\S]*filePreviewPopoutGenerationMatches\(path, previewWindow, generation\)/.test(source), 'preview pop-out writes an immediate snapshot and then a completed async snapshot through the same dispatch');
    assert.ok(/previewWindow\._yolomuxPreviewControlsCleanup[\s\S]*bind\(previewWindow, 'scroll', syncScroll\)[\s\S]*bind\(previewWindow, 'wheel', scheduleScrollSync\)[\s\S]*bind\(scroller, 'scroll', syncScroll\)[\s\S]*bind\(scroller, 'wheel', scheduleScrollSync\)/.test(source), 'preview pop-out window and scrolling element sync immediately on scroll and schedule next-frame sync on wheel without stale document listeners');
    assert.ok(/function scrollSyncTargetPosition\(from, to, axis = 'top'\)[\s\S]*const edgeSnap = Math\.max\(2, Math\.ceil\(sourceClient \* 0\.01\)\);[\s\S]*if \(maxTo <= 0 \|\| current <= edgeSnap\) return 0;[\s\S]*if \(maxFrom <= edgeSnap \|\| current >= maxFrom - edgeSnap\) return maxTo;[\s\S]*const sourceCenter = Math\.min\(maxFrom, current\) \+ \(sourceClient \/ 2\);[\s\S]*return Math\.min\(maxTo, Math\.max\(0, target\)\);/.test(source), 'pop-out scroll sync aligns viewport centers with fractional precision and explicit edge snaps');
    assert.ok(/function syncFilePreviewPopoutFromPanel[\s\S]*syncScrollPositionByRatio\(from, scroller\)/.test(source), 'editor-to-popout scroll sync uses the shared proportional mapper');
    assert.ok(/function syncFilePreviewPopoutScroll[\s\S]*syncScrollPositionByRatio\(scroller, editorScroller\)[\s\S]*syncScrollPositionByRatio\(scroller, previewPane\)/.test(source), 'popout-to-editor scroll sync uses the shared proportional mapper');
    assert.ok(/function fileEditorSourceElement\(panel, source\)\s*\{[\s\S]*fileEditorPanelMode\(panel\) === 'diff'[\s\S]*return null/.test(source), 'Differ views are not preview-scroll sources');
    assert.ok(/function syncFilePreviewPopoutScroll[\s\S]*mode !== 'diff' && editorScroller[\s\S]*syncScrollPositionByRatio\(scroller, editorScroller\)/.test(source), 'preview pop-out scrolling does not drive Differ editors');
    assert.ok(/function scheduleFilePreviewPopoutScrollSync\(path, previewWindow, options = \{\}\)[\s\S]*requestAnimationFrame\(run\)/.test(source), 'pop-out wheel/scroll sync is coalesced through requestAnimationFrame for smooth trackpad deltas');
    assert.ok(/function syncFileEditorInPaneSplitScroll\(host, source\)[\s\S]*syncFileEditorSplitScrollBySourceAnchors\(host, source, editorScroller, previewPane\)[\s\S]*return syncScrollPositionByRatio\(from, to\);/.test(source), 'split Preview scroll sync uses media-aware source anchors before falling back to the proportional mapper');
    const splitSyncStart = source.indexOf('function syncFileEditorInPaneSplitScroll');
    const splitSyncBody = source.slice(splitSyncStart, source.indexOf('\nfunction ', splitSyncStart + 1));
    assert.equal(/scrollPreviewToSourceLine|scrollIntoView/.test(splitSyncBody), false, 'split Preview scroll sync does not jump through hard source-line scrollIntoView calls');
    assert.ok(/function syncFileEditorSplitScrollBySourceAnchors[\s\S]*previewPaneNeedsSourceAnchorScroll[\s\S]*sourcePositionForEditorScroll[\s\S]*previewScrollTopForSourcePosition/.test(source), 'media-heavy split Preview scroll sync accounts for rendered image height through interpolated source anchors');
    assert.ok(/function previewSourceLineAnchors\(previewPane\)[\s\S]*previewSourceAnchorIsRendered\(item\.element\)/.test(source), 'split Preview source anchors ignore hidden collapsed-details content');
    assert.ok(/function previewSourceAnchorIsRendered\(element\)[\s\S]*elementHiddenByClosedDetails\(element\)/.test(source), 'split Preview source anchors detect closed details before measuring DOM rects');
    assert.ok(/function previewPaneNeedsSourceAnchorScroll\(previewPane\)[\s\S]*details, img\.markdown-preview-image/.test(source), 'split Preview uses source anchors when expandable details can change rendered height');
    assert.ok(/function fileEditorScrollSyncBlocked\(panel, source = ''\)[\s\S]*panel\?\._splitScrollSource !== source/.test(source), 'split Preview scroll guard suppresses only the opposite/programmatic side');
    assert.ok(/function setFileEditorScrollSyncGuardForSource\(source, \.\.\.panels\)[\s\S]*panel\._splitScrollSource = source \|\| ''/.test(source), 'split Preview scroll guard records the active driver pane');
    assert.ok(/function scheduleFileEditorSplitScrollSync\(host, source\)[\s\S]*host\._splitScrollPendingSource = source[\s\S]*requestAnimationFrame\(run\)/.test(source), 'split Preview scroll sync is coalesced through requestAnimationFrame for large-document trackpad deltas');
    assert.ok(/addEventListener\('scroll', \(\) => \{[\s\S]*scheduleFileEditorSplitScrollSync\(panel, 'editor'\);[\s\S]*scheduleFileEditorPanelViewStateCapture\(item, panel\);[\s\S]*\}\)/.test(source), 'editor scroll listener uses the scheduled split-preview sync path and records viewport state');
    assert.ok(/previewPane\?\.addEventListener\('scroll', \(\) => scheduleFileEditorSplitScrollSync\(panel, fileEditorPreviewScrollSyncSource\(panel\)\)\)/.test(source), 'preview scroll listener switches to editor-driven sync during preview layout changes');
    assert.ok(/previewPane\?\.addEventListener\('toggle'[\s\S]*event\.target\?\.matches\?\.\('details'\)[\s\S]*scheduleFileEditorPreviewLayoutSync\(panel\)[\s\S]*true\)/.test(source), 'Markdown details expansion/collapse re-centers split Preview from the editor source line');
    assert.ok(/previewPane\?\.addEventListener\('load'[\s\S]*img\.markdown-preview-image, img\.mermaid-preview-image[\s\S]*scheduleFileEditorPreviewLayoutSync\(panel\)[\s\S]*true\)/.test(source), 'late preview image loads re-center split Preview from the editor source line');
    assert.ok(/function syncFileEditorSplitScroll[\s\S]*syncFilePreviewPopoutsFromPanel\(host, source\)/.test(source), 'editor preview/editor scroll drives open preview pop-outs');
    assert.ok(/function closeFilePreviewPopout\(path\)[\s\S]*filePreviewPopouts\.delete\(path\)[\s\S]*previewWindow\.close\?\.\(\)/.test(source), 'preview pop-out close removes the registry entry and closes the window');
    assert.ok(/function setFileEditorViewMode\(path, mode, item = null\)[\s\S]*mode === 'preview' \|\| mode === 'split'[\s\S]*closeFilePreviewPopout\(path\)/.test(fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8')), 'switching to in-editor Preview or Split closes any open pop-out preview for that file');
    assert.ok(fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8').includes("if (typeof refreshFilePreviewPopouts === 'function') refreshFilePreviewPopouts();"), 'settings refresh syncs open preview pop-outs');
    const fileReloadSource = fs.readFileSync('static_src/js/yolomux/40_file_explorer_files.js', 'utf8') + fs.readFileSync('static_src/js/yolomux/45_file_explorer_actions.js', 'utf8');
    assert.ok(/function replaceOpenFileStateFromDisk[\s\S]*renderOpenFilePath\(path\);[\s\S]*updateFilePreviewPopout\(path, loaded\.state\.content \|\| ''\)/.test(fileReloadSource), 'external disk reload syncs open preview pop-outs');
    assert.ok(/function replaceOpenFileStateFromDisk[\s\S]*fileEditorTabItemsForPath\(path\)\.map[\s\S]*captureFileEditorPanelViewState\(item, panel\)[\s\S]*renderOpenFilePath\(path\);[\s\S]*restoreFileEditorPanelViewState\(item, panel\)[\s\S]*requestAnimationFrame/.test(fileReloadSource), 'external disk reload preserves per-editor cursor and scroll for every open tab of the path');
    assert.ok(fileReloadSource.includes('function openFileBackgroundReloadShouldDefer(path, state)') && fileReloadSource.includes('openFileBackgroundReloadDeferMs'), 'push/watch reloads are deferred during active editing and immediately after save');
    assert.ok(source.includes('position: static !important;'), 'preview pop-out resets the in-pane absolute preview positioning');
    assert.ok(source.includes('display: block !important;') && source.includes('grid-template-rows: none !important;'), 'preview pop-out resets the app body grid layout');
    assert.ok(source.includes('width: 100% !important;') && source.includes('left: auto !important;'), 'preview pop-out resets split-preview geometry that would clip content to the right half');
    const bootstrapSource = fs.readFileSync('static_src/js/yolomux/00_bootstrap_state.js', 'utf8');
    const dockviewSource = fs.readFileSync('static_src/js/yolomux/75_dockview_layout.js', 'utf8');
    const terminalBootSource = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
    const paneCss = fs.readFileSync('static_src/css/yolomux/40_layout_panes_tabs.css', 'utf8');
    assert.ok(/canPopout:\s*true,[\s\S]*popoutRenderer:\s*item => panePopoutPanelSnapshot\(item\)/.test(bootstrapSource), 'YO!info and YO!stats declare snapshot popout support in TAB_TYPES');
    assert.ok(/key:\s*'preferences'[\s\S]*popoutDisabledReason:\s*'interactive Preferences popout is disabled in phase 1'/.test(bootstrapSource) && /key:\s*'files'[\s\S]*popoutDisabledReason:\s*'interactive Finder\/Tabber popout is disabled in phase 1'/.test(bootstrapSource), 'interactive non-snapshot panes declare explicit phase-1 disabled popout reasons');
    assert.ok(/canPopout:\s*item =>[\s\S]*editorPreviewModeAvailable/.test(bootstrapSource) && /openPopout:\s*item =>[\s\S]*openFilePreviewPopout/.test(bootstrapSource), 'file editor popout capability routes through the existing preview popout');
    assert.ok(/dockviewHeaderActionsHtml\(item\)[\s\S]*popout:\s*paneCanPopout\(item\)/.test(dockviewSource), 'Dockview header actions show the popout control only for canPopout tab types');
    assert.ok(/button\.dataset\.panePopout[\s\S]*openPanePopout\(button\.dataset\.panePopout \|\| item\)/.test(dockviewSource), 'Dockview popout button dispatches through the shared openPanePopout helper');
    assert.ok(/function showTabContextMenu\(item, x, y, options = \{\}\)[\s\S]*paneCanPopout\(item\)[\s\S]*appendContextMenuButton\(menu, t\('tab\.popout'\), \(\) => openPanePopout\(item\), closeSessionContextMenu\)/.test(fs.readFileSync('static_src/js/yolomux/10_core_utils.js', 'utf8')), 'shared tab context menu gates Pop out through paneCanPopout and opens via openPanePopout');
    assert.ok(/showTabContextMenu\(item, event\.clientX, event\.clientY, \{tab: element\}\)/.test(dockviewSource), 'Dockview tabs use the shared tab context menu');
    assert.ok(/showTabContextMenu\(item, event\.clientX, event\.clientY, \{tab\}\)/.test(fs.readFileSync('static_src/js/yolomux/78_panel_shell.js', 'utf8')), 'pane tabs use the shared tab context menu');
    assert.ok(/showTabContextMenu\(tabItem, event\.clientX, event\.clientY, \{tab: tabRow\.querySelector\?\.\('\.tabber-session-tab'\) \|\| tabRow\}\)/.test(fs.readFileSync('static_src/js/yolomux/40_file_explorer_files.js', 'utf8')), 'Tabber rows use the shared tab context menu');
    assert.ok(/function paneFrameControlsHtml\(session, options = \{\}\)[\s\S]*includePopout[\s\S]*data-pane-popout/.test(terminalBootSource), 'shared pane-frame controls own the popout button markup');
    const layoutActionsSource = fs.readFileSync('static_src/js/yolomux/70_layout_actions.js', 'utf8');
    assert.ok(/function closePopoutsForLayoutItem\(item\)[\s\S]*closePanePopout\(item\)[\s\S]*closeFilePreviewPopout/.test(source), 'source pane cleanup routes generic and preview pop-outs through one shared close helper');
    assert.ok(/function removeSessionFromLayout\(item, options = \{\}\)[\s\S]*closePopoutsForLayoutItem\(item\)/.test(layoutActionsSource) && /function removePaneFromLayout\(item\)[\s\S]*moved\.forEach\(closePopoutsForLayoutItem\)/.test(layoutActionsSource), 'closing a tab or pane closes tracked pop-outs for those layout items');
    assert.ok(/window\.addEventListener\('beforeunload', closeAllPanePopouts\)/.test(source), 'app unload closes all tracked generic and preview pop-outs');
    assert.ok(/function renderInfoPanel\(options = \{\}\)[\s\S]*function renderInfoPanelMeasured\(node, options = \{\}\)[\s\S]*refreshPanePopouts\(infoItemId\)/.test(terminalBootSource), 'YO!info refreshes any matching detached pane pop-out snapshot');
    assert.ok(/\.tabs \.pane-popout[\s\S]*\.tabs \.pane-popout::before[\s\S]*\.tabs \.pane-popout::after/.test(paneCss), 'popout button uses the shared pane-control CSS path with its own glyph only');
  });

  test('t@7900', () => {
    // Preferences order is grouped by how the settings are used: general startup defaults, visual appearance,
    // terminal/editor behavior, notifications, file handling, polling/performance, then agent controls.
    const api = loadYolomux('', ['1']);
    api.setActiveLocaleForTest('en');
    const html = api.preferencesPanelHtmlForTest('');
    const sectionOrder = [...html.matchAll(/data-preference-section="([^"]+)"/g)].map(match => match[1]);
    const expectedOrder = [
      api.t('pref.section.general'),
      api.t('pref.section.appearance'),
      api.t('pref.section.terminal_editor'),
      api.t('pref.section.notifications'),
      api.fileExplorerLabel(),
      api.t('pref.section.uploads'),
      api.t('pref.section.performance'),
      api.t('pref.section.github'),
      api.t('pref.section.yoagent'),
      api.t('pref.section.share'),
      api.t('pref.section.yolo'),
    ];
    assert.deepStrictEqual(sectionOrder, expectedOrder, 'Preferences sections render in the grouped order');
    const yoagentIndex = sectionOrder.indexOf(api.t('pref.section.yoagent'));
    const shareIndex = sectionOrder.indexOf(api.t('pref.section.share'));
    const yoloIndex = sectionOrder.indexOf(api.t('pref.section.yolo'));
    assert.deepStrictEqual([yoagentIndex, shareIndex, yoloIndex], [sectionOrder.length - 3, sectionOrder.length - 2, sectionOrder.length - 1], 'YO!agent, YO!share, and YOLO sections stay adjacent at the end; YO!info has no standalone Preferences settings');
    const sectionHtml = title => {
      const start = html.indexOf(`data-preference-section="${title}"`);
      assert.ok(start >= 0, `${title} section renders`);
      const next = html.indexOf('data-preference-section="', start + 1);
      return next >= 0 ? html.slice(start, next) : html.slice(start);
    };
    assert.ok(sectionHtml(api.t('pref.section.notifications')).includes('data-setting-path="general.reload_on_update"'), 'server-version reload prompt is in Notifications');
    assert.ok(sectionHtml(api.t('pref.section.notifications')).includes('data-setting-path="general.reload_on_update_auto"'), 'server-version auto-reload is in Notifications');
    assert.equal(sectionHtml(api.t('pref.section.notifications')).includes('data-setting-path="updates.check_enabled"'), false, 'origin/main update check toggle is removed from Notifications');
    assert.ok(sectionHtml(api.t('pref.section.notifications')).includes('data-setting-path="updates.notify_level"'), 'origin/main update notification threshold is in Notifications');
    const shareHtml = sectionHtml(api.t('pref.section.share'));
    assert.ok(shareHtml.includes('data-setting-path="share.ttl_seconds"'), 'YO!share Preferences exposes the default share lifetime');
    assert.ok(shareHtml.includes('data-setting-path="share.max_viewers"'), 'YO!share Preferences exposes the default viewer cap');
    assert.ok(shareHtml.includes('data-setting-path="share.read_only"'), 'YO!share Preferences exposes the read-only default');
    assert.ok(/type="radio"[^>]*value="http"[^>]*data-setting-path="share\.scheme"[\s\S]*type="radio"[^>]*value="https"[^>]*data-setting-path="share\.scheme"/.test(shareHtml), 'YO!share Preferences exposes http/https protocol defaults');
    assert.equal(sectionHtml(api.t('pref.section.performance')).includes('data-setting-path="general.reload_on_update_auto"'), false, 'server-version auto-reload no longer lives in Performance');
    assert.equal(sectionHtml(api.t('pref.section.performance')).includes('data-setting-path="updates.check_enabled"'), false, 'origin/main update check no longer lives in Performance');
    assert.equal(sectionHtml(api.t('pref.section.yoagent')).includes('data-setting-path="yoagent.refresh_interval_seconds"'), false, 'YO!agent Preferences no longer exposes the background transcript-summary interval');
    const appearanceHtml = sectionHtml(api.t('pref.section.appearance'));
    assert.ok(appearanceHtml.includes('Global appearance'), 'Appearance shows the renamed Global appearance field');
    assert.ok(appearanceHtml.includes('Theme color'), 'Appearance shows the renamed Theme color field');
    assert.ok(appearanceHtml.includes('data-setting-path="general.default_layout"'), 'Default layout is in Appearance');
    assert.ok(/type="radio"[^>]*value="split"[^>]*data-setting-path="general\.default_layout"/.test(appearanceHtml), 'Default layout offers Split');
    assert.ok(appearanceHtml.includes('Single pane') && appearanceHtml.includes('Split') && appearanceHtml.includes('Grid'), 'Default layout labels match View layout labels');
    assert.equal(appearanceHtml.includes('Wall'), false, 'Wall is no longer offered as a default layout choice');
    assert.ok(appearanceHtml.includes('Envy green'), 'Active color Green is labeled Envy green');
    assert.ok(appearanceHtml.includes('Deep ocean blue'), 'Active color Blue is labeled Deep ocean blue');
    assert.ok(appearanceHtml.includes('Blood orange'), 'Active color Orange is labeled Blood orange');
    assert.ok(appearanceHtml.includes('Solar gold'), 'Active color Yellow is labeled Solar gold');
    assert.ok(appearanceHtml.includes('Royal violet'), 'Active color Purple is labeled Royal violet');
    assert.ok(appearanceHtml.includes('Moon white'), 'Active color White is labeled Moon white');
    assert.ok(appearanceHtml.includes('Signal green'), 'Cursor color Green is labeled Signal green');
    assert.ok(appearanceHtml.includes('Laser lime'), 'Cursor color Laser lime is available');
    assert.ok(appearanceHtml.includes('Neon green'), 'Cursor color Neon green is available');
    assert.ok(appearanceHtml.includes('Neon cyan'), 'Cursor color Neon cyan is available');
    assert.ok(appearanceHtml.includes('Neon magenta'), 'Cursor color Neon magenta is available');
    assert.ok(appearanceHtml.includes('Neon orange'), 'Cursor color Neon orange is available');
    assert.ok(appearanceHtml.includes('Electric azure'), 'Cursor color Blue is labeled Electric azure');
    assert.ok(appearanceHtml.includes('Flare orange'), 'Cursor color Orange is labeled Flare orange');
    assert.ok(appearanceHtml.includes('Lightning yellow'), 'Cursor color Yellow is labeled Lightning yellow');
    assert.ok(appearanceHtml.includes('Plasma violet'), 'Cursor color Purple is labeled Plasma violet');
    assert.ok(appearanceHtml.includes('Starlight white'), 'Cursor color White is labeled Starlight white');
    assert.ok(/type="radio"[^>]*value="blue"[^>]*data-setting-path="appearance\.active_color"/.test(appearanceHtml), 'Active color Blue renders as a radio');
    assert.equal(appearanceHtml.includes('data-setting-path="appearance.yolo_rotate_ms"'), false, 'Active YO rotation is removed from Appearance');
    assert.ok(/data-setting-path="appearance\.active_color"[\s\S]*data-setting-path="appearance\.separator_color"[\s\S]*data-setting-path="appearance\.editor_cursor_color"[\s\S]*data-setting-path="appearance\.date_time_hour_cycle"/.test(appearanceHtml), 'Separator and Cursor color sit immediately after Active color in Appearance, with no YO rotation row between them');
    assert.ok(/type="radio"[^>]*value="blue"[^>]*data-setting-path="appearance\.editor_cursor_color"/.test(appearanceHtml), 'Cursor color Blue renders as a radio');
    const preferencesSource = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/function layoutModePreferenceChoices\(\)\s*\{[\s\S]*layoutModeValues\.map\(value => \(\{value, label: t\(`menu\.view\.layout\.\$\{value\}`\)\}\)\)/.test(preferencesSource), 'Default layout choices derive from the shared View layout modes');
    assert.ok(/function activeColorPreferenceChoices\(\)\s*\{[\s\S]*UI_COLOR_CHOICES\.map\(value => activeColorPreferenceChoice\(value, t\(UI_COLOR_PRESETS\[value\]\.labelKey\)\)\)/.test(preferencesSource), 'Active color choices derive labels from the shared UI color parent');
    assert.ok(/function separatorColorPreferenceChoices\(\)[\s\S]*clientSettingsPayload\?\.choices\?\.\['appearance\.separator_color'\][\s\S]*SEPARATOR_COLOR_CHOICES[\s\S]*\.map\(separatorColorPreferenceChoice\)/.test(preferencesSource), 'Separator color choices sync to the backend allowlist with a local fallback');
    assert.ok(/function cursorColorPreferenceChoices\(\)\s*\{[\s\S]*clientSettingsPayload\?\.choices\?\.\['appearance\.editor_cursor_color'\][\s\S]*CURSOR_COLOR_CHOICES[\s\S]*\.map\(cursorColorPreferenceChoice\)/.test(preferencesSource), 'Cursor color choices sync to the backend allowlist with a local fallback');
    assert.ok(/function cursorColorPreferenceChoice\(value\)\s*\{[\s\S]*preset\?\.cursorLabelKey \? t\(preset\.cursorLabelKey\) : preferenceChoiceLabel\(value\)/.test(preferencesSource), 'Cursor color labels use cursor-specific bright color names from the shared parent');
    assert.ok(/preferences-radio-swatches joined[\s\S]*--preferences-radio-swatch:#3b82f6[\s\S]*--preferences-radio-swatch:#2563eb/.test(appearanceHtml), 'Active color Blue radio shows connected actual dark/light accent swatches');
    assert.ok(appearanceHtml.includes('preferences-setting-note') && appearanceHtml.includes('Editor/Terminal font sizes are in Terminal / Editor.'), 'Appearance shows a note after Finder font size pointing editor/terminal font sizes to Terminal / Editor');
    assert.ok(/data-setting-path="appearance\.file_explorer_font_size"[\s\S]*preferences-setting-note[\s\S]*data-setting-path="appearance\.tab_width"/.test(appearanceHtml), 'Appearance font-size note sits directly after Finder font size');
    assert.ok(/data-setting-path="appearance\.pane_ring_opacity"[^>]*data-setting-type="range"[^>]*min="5"[^>]*max="100"/.test(appearanceHtml), 'Pane ring opacity renders as a 5-100 Appearance slider');
    assert.equal(appearanceHtml.includes('data-setting-path="appearance.inactive_pane_gradient"'), false, 'Inactive pane gradient is removed from Appearance');
    assert.ok(/data-setting-path="appearance\.inactive_pane_opacity"[^>]*data-setting-type="range"[^>]*min="0"[^>]*max="100"/.test(appearanceHtml), 'Inactive pane opacity renders as a 0-100 Appearance slider');
    const appearancePaths = [...appearanceHtml.matchAll(/data-setting-path="([^"]+)"/g)].map(match => match[1]);
    assert.equal(appearancePaths.at(-1), 'appearance.date_time_hour_cycle', '12-hour / 24-hour Date/time clock is the last Appearance item');
    const terminalEditorHtml = sectionHtml(api.t('pref.section.terminal_editor'));
    assert.ok(terminalEditorHtml.includes('data-setting-path="appearance.terminal_theme"'), 'Terminal / Editor follows Appearance and owns terminal/editor-specific controls');
    assert.equal(terminalEditorHtml.includes('data-setting-path="appearance.editor_cursor_color"'), false, 'Cursor color moved out of Terminal / Editor into Appearance');
    assert.ok(/data-setting-path="appearance\.terminal_font_size"[\s\S]*data-setting-path="appearance\.editor_font_size"[\s\S]*data-setting-path="appearance\.preview_font_size"[\s\S]*data-setting-path="terminal_editor\.scrollback"/.test(terminalEditorHtml), 'Terminal / Editor groups Terminal, Editor, and Preview font sizes together before scrollback');
    assert.equal(sectionHtml(api.t('pref.section.general')).includes('data-setting-path="general.default_layout"'), false, 'Default layout no longer lives in General');
    assert.equal(sectionHtml(api.t('pref.section.general')).includes('data-setting-path="general.reload_on_update"'), false, 'Notify on server update no longer lives in General');
    // the GitHub section carries the watched-PRs list field.
    assert.ok(html.includes('data-setting-path="github.watched_prs"'), 'the GitHub section has the watched_prs list field');
    assert.equal(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))['pref.appearance.pane_ring_opacity.help'], 'Percent, 5–100. This is the ring drawn over the ACTIVE content edge; lower values make the green/red pane ring fainter.', 'Pane ring opacity help describes the ACTIVE content-edge ring');
    const settingsRuntimeSource = fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8');
    assert.ok(settingsRuntimeSource.includes("Math.max(5, Math.min(100, numberSetting('appearance.pane_ring_opacity', 75)))"), 'Pane ring opacity runtime clamp allows 5%');
    assert.ok(settingsRuntimeSource.includes("root.setProperty('--pane-active-ring-opacity', `${percent}%`)"), 'The active pane ring opacity follows the 5-100% preference');
    assert.equal(settingsRuntimeSource.includes('Math.max(75, paneRingOpacity)'), false, 'The active pane ring must not force a 75% floor');
    assert.equal(settingsRuntimeSource.includes('inactive_pane_gradient'), false, 'Inactive pane gradient is removed from runtime settings');
    assert.ok(settingsRuntimeSource.includes("applyInactivePaneOpacity(numberSetting('appearance.inactive_pane_opacity', 60))"), 'Inactive pane opacity defaults to 60% in runtime settings');
    assert.ok(fs.readFileSync('yolomux_lib/settings.py', 'utf8').includes('("appearance", "pane_ring_opacity"): (5, 100)'), 'Pane ring opacity server settings clamp allows 5%');
    assert.equal(fs.readFileSync('yolomux_lib/settings.py', 'utf8').includes('inactive_pane_gradient'), false, 'Inactive pane gradient is removed from server settings');
    assert.ok(fs.readFileSync('yolomux_lib/settings.py', 'utf8').includes('("appearance", "inactive_pane_opacity"): (0, 100)'), 'Inactive pane opacity server settings clamp is 0-100');
  });

  test('t@7980', () => {
    // the block cursor fills the full monospace cell (width: 1ch), not a fat line.
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/body\.editor-cursor-block[^{]*\.cm-cursor[\s\S]*?\{[\s\S]*?width: 1ch !important;/.test(css), '#122: the block editor cursor is one full character cell wide (1ch)');
  });

  test('t@7986', () => {
    // the Preferences global-reset UI (title, warning, both buttons, per-row Reset) is localized.
    const api = loadYolomux('', ['1']);
    const zhHant = JSON.parse(fs.readFileSync('static/locales/zh-Hant.json', 'utf8'));
    api.i18nSetCatalogForTest('zh-Hant', zhHant);
    api.setActiveLocaleForTest('zh-Hant');
    // A non-default value makes the global-reset block render (it is hidden when everything is default).
    api.setClientSettingsPatchForTest({appearance: {ui_font_size: 19}});
    const html = api.preferencesPanelHtmlForTest('');
    assert.ok(html.includes(zhHant['pref.reset.title']), '#115: the global-reset title is localized');
    assert.ok(html.includes(zhHant['pref.reset.all']), '#115: the "Reset all defaults" button is localized');
    assert.ok(html.includes(`aria-label="${zhHant['pref.reset.aria']}"`), '#115: the reset group aria-label is localized');
    assert.ok(html.includes(`>${zhHant['pref.reset.row']}</button>`), '#115: the per-row Reset button is localized');
    // No bare English reset literals leak through.
    assert.ok(!/>Global reset<|>Reset all defaults<|>Continue reset</.test(html), '#115: no English reset literals leak in a non-English locale');
    // Source guard: every reset literal routes through t('pref.reset.*').
    const src = fs.readFileSync('static/yolomux.js', 'utf8');
    for (const key of ['title', 'confirmTitle', 'warning', 'confirmWarning', 'continue', 'cancel', 'all', 'row', 'aria']) {
      assert.ok(src.includes(`t('pref.reset.${key}'`), `#115: reset UI uses t('pref.reset.${key}')`);
    }
  });

  test('t@8008', () => {
    // the menu bar, Modified-files panel, diff-ref, and comparison localize in a non-English
    // locale and leak no bare English; source guards confirm the builders route through t().
    const api = loadYolomux('', ['1']);
    const zhHant = JSON.parse(fs.readFileSync('static/locales/zh-Hant.json', 'utf8'));
    api.i18nSetCatalogForTest('zh-Hant', zhHant);
    api.setActiveLocaleForTest('zh-Hant');
    api.setFileExplorerSessionFilesPayloadForTest({
      session: '1',
      loaded: true,
      errors: [],
      refs_by_repo: {'/repo/app': [{ref: 'abc123def456', short: 'abc123d', subject: 'older base commit'}]},
      repos: [{repo: '/repo/app', count: 1, touched_count: 1, added: 2, removed: 1, behind: 0, ahead: 1}],
      files: [{session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'README.md', abs_path: '/repo/app/README.md', mtime: 100, added: 2, removed: 1}],
    });
    const panel = api.fileExplorerChangesPanelHtml();
    // C7: the embedded title now names the session via changes.titleForSession; assert its localized stems
    // surround the session (independent of the session label value).
    const titleStems = zhHant['changes.titleForSession'].split('{session}');
    const escHtml = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    assert.ok(titleStems.every(stem => !stem || panel.includes(escHtml(stem))), '#121/C7: the Modified-files title is localized and names the session');
    assert.ok(panel.includes(`>${zhHant['changes.refresh']}</button>`), '#121: the Modified-files Refresh button is localized');
    // C6/C15 follow-up: the FROM/TO text pickers are inline in each repo's localized comparison sentence
    // (no separate FROM/TO labels); assert the sentence's localized text stems surround the inputs.
    for (const stem of zhHant['diff.comparing'].split(/\{from\}|\{to\}/).map(s => s.trim()).filter(Boolean)) {
      assert.ok(panel.includes(stem), `#121/C15: the inline comparison sentence is localized ("${stem}")`);
    }
    assert.ok(/changes-repo-refs compact[\s\S]*data-diff-ref-from[\s\S]*data-diff-ref-to/.test(panel), '#121/C15: the FROM/TO text pickers are present inline on the repo comparison line');
    assert.ok(panel.includes(`aria-label="${zhHant['diff.ref.from.aria']}"`), '#121: the FROM picker aria-label is localized');
    assert.ok(panel.includes(zhHant['changes.ahead.one'].replace('{count}', '1')), '#121: the Ahead-N-commit meta is localized (tPlural)');
    api.setDiffRefsByRepoForTest('/repo/app', {from: 'abc123def456', to: 'current'});
    const compactPanel = api.fileExplorerChangesPanelHtml();
    assert.match(compactPanel, /data-diff-ref-from[^>]*value="abc123d"|value="abc123d"[^>]*data-diff-ref-from/, '#121/C15: selected refs render as a short SHA without branch aliases');
    assert.deepEqual(api.diffRefPopoverSubjectPartsForTest({subject: 'Fix graph update (#123)'}), {description: 'Fix graph update', pr: '(#123)'}, '#121/C15: the picker keeps a PR number visible beside the description');
    assert.deepEqual(api.diffRefPopoverSubjectPartsForTest({ref: 'HEAD', aliases: ['HEAD', 'origin/main', 'main', 'keivenc/some-branch'], subject: 'Fix graph update (#123)'}), {description: '[origin/main] [main] [keivenc/some-branch] Fix graph update', pr: '(#123)'}, '#121/C15: the HEAD picker row shows all same-commit branch aliases as bracketed labels before its commit description');
    assert.equal(api.diffRefCompactDisplayForTest({ref: 'abc123def456', short: 'abc123d/HEAD main', commit: 'abc123def456'}), 'abc123d', '#121/C15: compact ref display omits /HEAD and branch aliases');
    // No bare English leaks in the localized Modified-files panel.
    assert.ok(!/>Modified files<|>Refresh<|>FROM <|>TO <|Ahead 1 commit|Comparing /.test(panel), '#121: no English leaks in the localized Modified-files panel');
    // Source guards: the menu/changes builders carry no bare English literals (all via t()).
    const appSrc = fs.readFileSync('static/yolomux.js', 'utf8');
    for (const literal of ["menuCommand('Open file'", "menuCommand('Preferences'", "menuCommand('Log out'", "menuCommand('Refresh'", "menuSubmenu('Theme'", "menuCommand('Info Bar'", "menuCommand('No matching tabs'", "'Kill tmux session", "class=\"changes-title\">Modified files<", '>FROM <select', '`Comparing ${esc(from)} to ${esc(to)}`']) {
      assert.equal(appSrc.includes(literal), false, `#121: bare English literal removed: ${literal}`);
    }
    // The pseudo-locale transforms a representative menu key (the completeness signal).
    const enXA = JSON.parse(fs.readFileSync('static/locales/en-XA.json', 'utf8'));
    assert.ok(/[⟦⟧]/.test(enXA['menu.file.openFile']) && !/^Open file$/.test(enXA['menu.file.openFile']), '#121: menu keys are pseudo-localized in en-XA');
  });

  test('t@8050', () => {
    // the default (files-mode) search bar blends matching commands/tabs into the results.
    const api = loadYolomux('', ['1']);
    const prefsLabel = api.itemLabel(api.prefsItemId);
    api.setFileQuickOpenCandidatesForTest('/repo/app', [
      {name: 'notes.py', path: '/repo/app/notes.py', relative_path: 'notes.py'},
    ]);
    api.setCommandPaletteStateForTest('files', prefsLabel);
    assert.ok(api.commandPaletteItems().some(item => item.group === 'Tabs' && item.label === prefsLabel), '#7: a command/tab matching a plain files-mode query is blended in (no > needed)');
    api.setCommandPaletteStateForTest('command', 'notes');
    assert.ok(api.commandPaletteItems().some(item => item.category === 'file' && item.path === '/repo/app/notes.py'), 'DOIT.55: command-mode queries also blend matching file-index results');
    // `>` stays commands-only — no file candidates blended.
    api.setCommandPaletteStateForTest('files', `>${prefsLabel}`);
    assert.ok(!api.commandPaletteItems().some(item => item.path === '/repo/app/notes.py'), '#7: the > prefix stays commands-only');
    // An empty files-mode query must NOT dump the whole command corpus.
    api.setCommandPaletteStateForTest('files', '');
    assert.ok(!api.commandPaletteItems().some(item => item.group === 'Tabs'), '#7: empty files-mode query shows files only (no command dump)');
    // `@` stays reserved for symbols (no command blend).
    api.setCommandPaletteStateForTest('files', '@thing');
    assert.ok(!api.commandPaletteItems().some(item => item.group === 'Tabs'), '#7: @ stays reserved for symbols');
  });

  // macOS Finder list-view keyboard PARITY. The key->intent map is a PURE function, unit-tested here so the
  // full set of bindings is verified as behavior (not just source shape). Works for Finder AND Differ.
  test('t@8072', () => {
    const api = loadYolomux();
    const I = (key, mods = {}) => api.fileExplorerKeyIntent(key, {shift: !!mods.shift, mod: !!mods.mod, alt: !!mods.alt});
    // move + extend
    assert.equal(I('ArrowDown'), 'move-down', 'Down moves selection');
    assert.equal(I('ArrowUp'), 'move-up', 'Up moves selection');
    assert.equal(I('ArrowDown', {shift: true}), 'extend-down', 'Shift+Down extends');
    assert.equal(I('ArrowUp', {shift: true}), 'extend-up', 'Shift+Up extends');
    assert.equal(I('Home'), 'move-home');
    assert.equal(I('End'), 'move-end');
    assert.equal(I('Home', {shift: true}), 'extend-home');
    assert.equal(I('End', {shift: true}), 'extend-end');
    // expand / collapse / parent / child
    assert.equal(I('ArrowRight'), 'expand', 'Right expands / steps in');
    assert.equal(I('ArrowLeft'), 'collapse', 'Left collapses / steps out');
    // open + enclosing folder
    assert.equal(I('ArrowDown', {mod: true}), 'open', 'Cmd-Down = open');
    assert.equal(I('o', {mod: true}), 'open', 'Cmd-O = open');
    assert.equal(I('O', {mod: true}), 'open');
    assert.equal(I('ArrowUp', {mod: true}), 'enclosing', 'Cmd-Up = enclosing folder');
    // rename / select-all / preview / type-ahead
    assert.equal(I('Enter'), 'rename', 'Return = rename (Finder)');
    assert.equal(I('a', {mod: true}), 'select-all', 'Cmd-A = select all');
    assert.equal(I(' '), 'preview', 'Space = Quick Look preview');
    assert.equal(I('d'), 'typeahead', 'a letter is type-to-select');
    assert.equal(I('R'), 'typeahead');
    // NOT claimed (left for the OS / other shortcuts)
    assert.equal(I('ArrowRight', {mod: true}), null, 'Cmd-Right is not claimed');
    assert.equal(I('Enter', {mod: true}), null);
    assert.equal(I('Enter', {shift: true}), null);
    assert.equal(I('ArrowDown', {alt: true}), null, 'Alt combos not claimed');
    assert.equal(I(' ', {mod: true}), null);
    assert.equal(I('Tab'), null);
    assert.equal(I('Escape'), null);
    assert.equal(I('x', {mod: true}), null, 'Cmd-X is not a Finder nav key here');
  });

  // Source guards: the dispatcher wires each intent to the right live-tree action.
  test('t@8110', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(source.includes('function handleFileExplorerArrowNav('), 'arrow-nav handler exists');
    assert.ok(source.includes('function fileExplorerKeyIntent('), 'pure key->intent map exists');
    assert.ok(/if \(handleFileExplorerArrowNav\(event\)\) return;/.test(source), 'wired into the global keydown after the delete shortcut');
    assert.ok(source.includes('!eventTargetIsFileExplorerSurface(event.target) && !isFileExplorerItem(focusedPanelItem)'), 'gated on the Finder/Differ surface');
    assert.ok(/const finderTreeInteractionController = createSharedTreeInteractionController\(\{[\s\S]*name: 'finder'/.test(source), 'Finder uses the shared tree interaction controller');
    assert.ok(source.includes('function fileTreeDirectoryExpanded(') && source.includes('function setFileTreeDirectoryExpanded('), 'one shared expand/collapse parent for both surfaces');
    assert.ok(/setFileTreeDirectoryExpanded[\s\S]{0,260}closest\('\.file-explorer-changes-panel'\)[\s\S]{0,220}changesFolderCollapsed[\s\S]{0,220}expandDirectoryRow/.test(source), 'the parent dispatches Differ (changesFolderCollapsed) vs Finder (expandDirectoryRow) — no per-surface key code');
    assert.ok(/finderTreeInteractionController = createSharedTreeInteractionController\(\{[\s\S]*setExpanded\(row, expanded\)[\s\S]*setFileTreeDirectoryExpanded\(row, path, expanded === true\)/.test(source), 'Right/Left route through the shared controller expand/collapse parent');
    assert.ok(/intent === 'open'/.test(source) && source.includes('openChangedFileInDiff(') && source.includes('openFileInEditor(leadPath, entry)') && source.includes('openFileExplorerManualRoot(leadPath)'), 'open: Differ file -> reusable collapsed diff, file -> editor, Finder folder -> descend');
    assert.ok(/intent === 'enclosing'[\s\S]{0,300}openFileExplorerManualRoot\(parent\)/.test(source), 'Cmd-Up opens the enclosing folder');
    assert.ok(/intent === 'rename'[\s\S]{0,200}beginFileTreeRename\(leadRow, leadPath, entry\)/.test(source), 'Enter renames the lead row (Finder AND Differ)');
    assert.ok(!/openChangeFile !== undefined\) return false/.test(source), 'no Differ-rename exclusion — Differ rows rename too (git mv handles tracked files)');
    assert.ok(/intent === 'preview'[\s\S]{0,300}openFileImagePreview\(leadRow, leadPath, entry\)/.test(source), 'Space previews (Quick Look) the lead file');
    assert.ok(source.includes('expandDirectoryRow(row, fullPath, {manual: true})') && source.includes('collapseDirectoryRow(row, fullPath, {manual: true})'), 'Finder branch of the shared parent still uses expand/collapseDirectoryRow');
    assert.ok(/function sharedTreeChildRow\(rows, row\)[\s\S]*pathIsInsideDirectory\(childId, id\)/.test(source), 'Right steps into the first child when already expanded through the shared parent');
    assert.ok(/function sharedTreeParentRow\(rows, row\)[\s\S]*rows\.find\(item => sharedTreeRowId\(item\) === parent\)/.test(source), 'Left steps to the parent row through the shared parent');
    assert.ok(source.includes('function fileExplorerTypeaheadSelect('), 'type-ahead selection exists');
    assert.ok(source.includes('fileExplorerSelectionLead = fullPath'), 'click/range selection seeds the same lead');
    assert.ok(source.includes('fileTreeRepoPopoverCursor.x + 14'), 'repo-row hover popover anchors to the RIGHT of the cursor');
  });
}

module.exports = {runEditorPreviewSuite};

if (require.main === module) {
  runSuites([runEditorPreviewSuite]);
}
