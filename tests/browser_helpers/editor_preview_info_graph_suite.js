const {
  assert,
  fs,
  loadYolomux,
  canonical,
  test,
  testAsync,
} = require('./layout_test_helper');

test('YO!info prefers the normalized work graph without duplicating a shared worktree branch inventory', () => {
  const source = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
  assert.ok(source.includes('function infoHasWorkGraph(info = {})'), 'YO!info recognizes a schema-valid canonical graph explicitly');
  assert.ok(source.includes('if (!infoHasWorkGraph(info)) return [];'), 'YO!info returns no Git sources without a normalized graph');
  assert.equal(source.includes('info?.project'), false, 'YO!info has no legacy project projection fallback');
  assert.ok(source.includes('function infoSourceBranches(source = {})'), 'graph branches use a shared graph-native selector');
  assert.ok(source.includes('source.workGraph\n          ? `${source.localRepositoryId}'), 'graph rows deduplicate using canonical local repository and branch IDs');
  assert.ok(source.includes('function infoGraphTabAgentsForSource(source = {})'), 'YO!info derives tab/actor attribution from graph associations');
  assert.ok(source.includes('workGraph.path_observations?.[id]?.git_worktree_id === worktreeId'), 'actor attribution uses canonical observation-to-worktree edges');
});

test('YO!info canonical relationship records ignore conflicting legacy projections and keep an empty graph empty', () => {
  const api = loadYolomux('', ['graph-session']);
  const graph = {
    version: 1,
    generation: 7,
    tmux_sessions: {'tmux-session:graph-session': {id: 'tmux-session:graph-session', name: 'graph-session'}},
    tmux_windows: {'tmux-window:graph-session:0': {id: 'tmux-window:graph-session:0', tmux_session_id: 'tmux-session:graph-session', index: '0', name: 'claude'}},
    tmux_panes: {'tmux-pane:graph-session:0.0': {id: 'tmux-pane:graph-session:0.0', tmux_window_id: 'tmux-window:graph-session:0', index: '0', target: '%graph', active: true, window_active: true}},
    runtime_actors: {'actor:graph': {id: 'actor:graph', tmux_pane_id: 'tmux-pane:graph-session:0.0', kind: 'claude', cwd: '/canonical/right', status: 'working', path_observation_ids: ['observation:graph']}},
    path_observations: {'observation:graph': {id: 'observation:graph', runtime_actor_id: 'actor:graph', git_worktree_id: 'worktree:graph', path: '/canonical/right', source: 'transcript', last_observed_at: 42}},
    git_worktrees: {'worktree:graph': {id: 'worktree:graph', root: '/canonical/right', git_dir: '/canonical/right/.git', local_repository_id: 'local:graph', hosted_repository_id: 'hosted:graph', current_branch_id: 'branch:graph'}},
    local_repositories: {'local:graph': {id: 'local:graph', common_git_dir: '/canonical/.git', local_branch_ids: ['branch:graph']}},
    hosted_repositories: {'hosted:graph': {id: 'hosted:graph', url: 'https://github.test/canonical/right'}},
    local_branches: {'branch:graph': {id: 'branch:graph', local_repository_id: 'local:graph', name: 'canonical-branch', subject: 'canonical subject', pull_request_ids: ['pr:80'], linear_issue_ids: ['linear:CAN-1'], pull_request_lookup_state: 'ready'}},
    pull_requests: {'pr:80': {id: 'pr:80', hosted_repository_id: 'hosted:graph', number: 80, title: 'canonical PR', state: 'open', url: 'https://github.test/canonical/right/pull/80', linear_ids: ['CAN-1'], local_branch_ids: ['branch:graph']}},
    linear_issues: {'linear:CAN-1': {id: 'linear:CAN-1', identifier: 'CAN-1', title: 'canonical issue', url: 'https://linear.test/CAN-1'}},
    worktree_branch_activity: {},
  };
  const legacyProject = {
    git: {root: '/legacy/wrong', branch: 'legacy-branch', other_branches: {branches: [{name: 'legacy-branch', current: true, pull_request: {number: 999, url: 'https://legacy.test/pull/999'}}]}},
    repos: [],
  };
  api.setTranscriptInfoForTest('graph-session', {work_graph: graph, project: legacyProject, window_metadata: [{git: legacyProject.git}]});
  api.setTranscriptSessionOrderForTest(['graph-session']);
  const records = api.infoRelationshipRecords();
  assert.equal(records.length, 1, 'one canonical graph branch creates one relationship record');
  const [record] = records;
  assert.equal(record.pathKey, '/canonical/right');
  assert.equal(record.branchKey, 'canonical-branch');
  assert.equal(record.prNumber, 80);
  assert.equal(record.gitWorktreeKey, 'worktree:graph');
  assert.equal(record.localRepositoryKey, 'local:graph');
  assert.equal(record.hostedRepositoryKey, 'hosted:graph');
  assert.equal(JSON.stringify(records).includes('/legacy/wrong'), false, 'legacy path projection cannot leak into a graph-backed result');
  assert.equal(JSON.stringify(records).includes('legacy-branch'), false, 'legacy branch projection cannot leak into a graph-backed result');
  assert.equal(JSON.stringify(records).includes('#999'), false, 'legacy PR projection cannot leak into a graph-backed result');

  const empty = {...graph, git_worktrees: {}, local_repositories: {}, local_branches: {}, hosted_repositories: {}, pull_requests: {}, linear_issues: {}, path_observations: {}, runtime_actors: {}, tmux_sessions: {}, tmux_windows: {}, tmux_panes: {}, worktree_branch_activity: {}};
  api.setTranscriptInfoForTest('graph-session', {work_graph: empty, project: legacyProject, window_metadata: [{git: legacyProject.git}]});
  assert.deepStrictEqual(canonical(api.infoRelationshipRecords()), [], 'a schema-valid empty graph does not revive stale legacy rows');
});

testAsync('metadata refresh retains a complete graph during lightweight payloads and rejects an older graph generation', async () => {
  const api = loadYolomux('', ['graph-refresh']);
  const completeGraph = {version: 1, generation: 20, tmux_sessions: {}, tmux_windows: {}, tmux_panes: {}, runtime_actors: {}, path_observations: {}, git_worktrees: {'worktree:complete': {id: 'worktree:complete', root: '/complete', local_repository_id: 'repo:complete'}}, local_repositories: {'repo:complete': {id: 'repo:complete', local_branch_ids: []}}, hosted_repositories: {}, local_branches: {}, pull_requests: {}, linear_issues: {}, worktree_branch_activity: {}};
  await api.applySessionMetadataPayloadForTest({session_order: ['graph-refresh'], sessions: {'graph-refresh': {work_graph: completeGraph, metadata_loading: false}}}, {refreshAuto: false, refreshActivity: false, refreshContext: false});
  await api.applySessionMetadataPayloadForTest({metadata_loading: true, session_order: ['graph-refresh'], sessions: {'graph-refresh': {work_graph: {version: 1, generation: 0, loading: true}, metadata_loading: true}}}, {refreshAuto: false, refreshActivity: false, refreshContext: false});
  assert.equal(api.transcriptMetadataStateForTest().payload.sessions['graph-refresh'].work_graph.generation, 20, 'a lightweight refresh retains the last complete canonical graph instead of reviving a legacy projection');
  const accepted = await api.applySessionMetadataPayloadForTest({session_order: ['graph-refresh'], sessions: {'graph-refresh': {work_graph: {...completeGraph, generation: 19}, metadata_loading: false}}}, {refreshAuto: false, refreshActivity: false, refreshContext: false});
  assert.equal(accepted, false, 'a late older graph generation cannot overwrite the newer canonical session graph');
  assert.equal(api.transcriptMetadataStateForTest().payload.sessions['graph-refresh'].work_graph.generation, 20, 'the newer graph remains active after stale delivery');
});
