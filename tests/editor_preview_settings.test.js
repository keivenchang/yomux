const {assert, fs, vm, test, runSuites} = require('./browser_helpers/layout_test_helper');
const {runEditorPreviewSuite} = require('./browser_helpers/editor_preview_suite');

test('pulse synchronization and restart discover animations before changing their timelines', () => {
  const source = fs.readFileSync('static_src/js/yomux/35_agent_window_activity.js', 'utf8');
  const code = source.slice(source.indexOf('function collectAgentWindowPulseNodes('), source.indexOf('function scheduleAgentWindowActivityAnimationSync('));
  let dirty = false;
  let flushes = 0;
  let writes = 0;
  const nodes = Array.from({length: 20}, () => ({
    classList: {contains: name => name === 'status-indicator'},
    querySelector: () => null,
    closest: () => null,
    style: {getPropertyValue: () => '', removeProperty: () => { dirty = true; }},
    getAnimations() {
      if (dirty) { flushes += 1; dirty = false; }
      return [{
        animationName: 'pulse',
        effect: {getTiming: () => ({duration: 1000, delay: -250})},
        set startTime(value) { assert.equal(value, -750); writes += 1; dirty = true; },
        cancel() { dirty = true; },
        play() { dirty = true; },
      }];
    },
  }));
  const context = {
    document: {querySelectorAll: () => nodes}, Date, performance: {timeOrigin: 1000},
    agentWindowActivityPulseSelector: 'pulse',
    statusPulseAnimationEnabled: () => true,
    attentionAnimationClockDelay: () => '',
    attentionAnimationDurationMs: () => 1000,
    agentWindowActivityAnimationUsesGlobalPhase: () => true,
  };
  vm.runInNewContext(code + ';syncAgentWindowActivityAnimationDelays();', context);
  assert.equal(writes, 20);
  assert.equal(flushes, 0, 'timeline writes cannot force layout before the next marker discovery');
  dirty = false;
  flushes = 0;
  vm.runInNewContext('restartAgentWindowActivityPulseAnimations();', context);
  assert.equal(flushes, 1, 'restart has one discovery pass after all cancellation/play writes');
  assert.equal(writes, 40);
});

test('pulse mutation scheduling retains its subtree and discovers all frame scopes before writes', () => {
  const source = fs.readFileSync('static_src/js/yomux/35_agent_window_activity.js', 'utf8');
  const code = source.slice(source.indexOf('function mutationTouchesAgentWindowActivity('), source.indexOf('function agentWindowActivityTransitionKey('));
  const frames = [];
  let observeMutation;
  let documentQueries = 0;
  let graphQueries = 0;
  let dirty = false;
  let discoveries = 0;
  let writes = 0;
  const pulse = {
    matches: () => true,
    contains: () => false,
    querySelectorAll: () => [],
    querySelector: () => null,
    closest: () => null,
    classList: {contains: name => name === 'js-debug-live-pulse'},
    style: {getPropertyValue: () => ''},
    getAnimations() {
      assert.equal(dirty, false, 'all scopes must be discovered before timeline writes');
      discoveries += 1;
      return [{animationName: 'agent-status-opacity-pulse',
        effect: {getTiming: () => ({duration: 1000})},
        set startTime(value) { assert.equal(value, -1000); dirty = true; writes += 1; },
      }];
    },
  };
  const sibling = {...pulse};
  const graph = {
    contains: node => node === pulse,
    querySelectorAll: () => { graphQueries += 1; return [pulse]; },
  };
  const context = {
    document: {body: {}, querySelectorAll: () => { documentQueries += 1; return []; }},
    Date, Number, Set, performance: {timeOrigin: 1000},
    MutationObserver: function(callback) { observeMutation = callback; this.observe = () => {}; },
    requestAnimationFrame: callback => { frames.push(callback); return frames.length; },
    agentWindowActivityMutationObserver: null, agentWindowActivityAnimationSyncFrame: 0,
    agentWindowActivityAnimationSyncDocument: false, agentWindowActivityAnimationSyncRoots: new Set(),
    agentWindowActivityPulseSelector: 'pulse',
    agentWindowActivityPulseAnimationNames: new Set(['agent-status-opacity-pulse']),
    attentionAnimationDelay: () => '', attentionAnimationDurationMs: () => 1000,
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  context.scheduleAgentWindowActivityAnimationSync(graph);
  observeMutation([{target: graph, addedNodes: [pulse]}]);
  context.scheduleAgentWindowActivityAnimationSync(pulse);
  context.scheduleAgentWindowActivityAnimationSync(sibling);
  assert.equal(frames.length, 1);
  frames.shift()();
  assert.equal(documentQueries, 0, 'a graph mutation cannot supersede its scoped work');
  assert.equal(graphQueries, 1, 'overlapping scopes must be queried once');
  assert.equal(discoveries, 2, 'matching roots participate even without matching descendants');
  assert.equal(writes, 2);
});

runSuites([() => runEditorPreviewSuite({shardIndex: 2, shardCount: 3})]);
