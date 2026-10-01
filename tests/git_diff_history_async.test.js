const {assert, fs, deferredFetch, controllableSseResponse, sseFrame: rawSseFrame, loadYolomux, jsonResponse: rawJsonResponse, flushAsyncWork, canonical, test, testAsync, runSuites} = require('./browser_helpers/layout_test_helper');

const withBoundary = payload => payload && typeof payload === 'object'
  && (typeof payload.repo === 'string' || Array.isArray(payload.commits))
  ? {shallow_boundary: 'complete', ...payload}
  : payload;
const jsonResponse = (payload, status) => rawJsonResponse(withBoundary(payload), status);
const sseFrame = (event, payload) => rawSseFrame(event, withBoundary(payload));

function installVisibleGitDiffPanel(api, item) {
  const state = api.gitDiffTabStateForTest(item);
  api.setGitDiffTabStateForTest(item, {visibleCommitCount: state.commits.length, shallowBoundary: state.shallowBoundary || 'complete'});
  const panel = api.createGitDiffPanelForTest(item);
  api.setPanelNodeForTest(item, panel);
  api.renderGitDiffPanelForTest(item, {panel});
  return panel;
}

async function runGitDiffHistorySuite() {
  test('Git history stats cannot publish across a changed shallow boundary', () => {
    const api = loadYolomux('', ['1']);
    const item = api.gitDiffItemFor('/repo');
    const sha = 'a'.repeat(40);
    const state = api.setGitDiffTabStateForTest(item, {shallowBoundary: 'current', commits: [{sha}]});
    assert.throws(() => api.applyGitDiffStatsPayloadForTest(item, state, {
      shallow_boundary: 'old', commits: [{sha, files: 2, added: 2, removed: 0, binary_files: 0}],
    }), error => error.code === 'git_history_stale');
    assert.equal(state.statsBySha.size, 0, 'rejected counts never enter the SHA cache');
    assert.equal(state.commits[0].files, undefined, 'rejected counts never reach rendered rows');
  });

  test('Git history products without a shallow boundary fail closed', () => {
    const api = loadYolomux('', ['1']);
    const item = api.gitDiffItemFor('/repo');
    const sha = 'a'.repeat(40);
    const state = api.setGitDiffTabStateForTest(item, {shallowBoundary: '', commits: [{sha}]});
    assert.throws(() => api.applyGitDiffStatsPayloadForTest(item, state, {
      commits: [{sha, files: 1, added: 1, removed: 0, binary_files: 0}],
    }), error => error.code === 'git_history_stale');
  });

  test('Git history recognizes typed Go SSE stale errors', () => {
    const api = loadYolomux('', ['1']);
    assert.equal(api.gitDiffHistorySnapshotIsStaleForTest({
      payload: {error_code: 'git_history_stale', message_key: 'fs.error.gitHistoryStale'},
    }), true);
  });

  await testAsync('Git history append refreshes when the shallow boundary changes without HEAD moving', async () => {
    const api = loadYolomux('', ['1']);
    const item = api.gitDiffItemFor('/repo');
    const sha = 'a'.repeat(40);
    const state = api.setGitDiffTabStateForTest(item, {
      repo: '/repo', repositoryIdentity: 'repo-a', shallowBoundary: 'old', head: sha,
      commits: [{sha, files: 2, added: 2, removed: 0, binary_files: 0}],
      loaded: true, nextCursor: 'older', snapshotCursor: 'snapshot',
    });
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      return Promise.resolve(jsonResponse({
        path: '/repo', repo: '/repo', repository_identity: 'repo-a', shallow_boundary: 'new',
        relative_path: '', head: sha, commits: [{sha}], next_cursor: '', snapshot_cursor: 'current',
      }));
    });
    assert.equal(await api.refreshGitDiffHistoryForTest(item, {append: true}), true);
    assert.equal(requests.length, 2, 'the changed interpretation replaces the frozen page through one full refresh');
    assert.match(requests[0], /cursor=older/);
    assert.doesNotMatch(requests[1], /cursor=/);
    assert.equal(state.shallowBoundary, 'new');
    assert.equal(state.commits[0].files, undefined, 'the old count cannot survive the refreshed page');
  });

  for (const changedBoundary of [false, true]) {
    await testAsync(`Git history refresh ${changedBoundary ? 'invalidates' : 'reuses'} same-SHA products across shallow boundaries`, async () => {
      const api = loadYolomux('', ['1']);
      const item = api.gitDiffItemFor('/repo');
      const sha = 'a'.repeat(40);
      const state = api.setGitDiffTabStateForTest(item, {
        path: '/repo', repo: '/repo', repositoryIdentity: 'repo-a', shallowBoundary: 'shallow',
        head: sha, loaded: true, loadAttempted: true,
        commits: [{sha, files: 2, added: 2, removed: 0, binary_files: 0}],
      });
      const record = api.gitDiffCommitRecordForTest(state, sha);
      record.detail = {sha, files: [{path: 'one'}, {path: 'two'}]};
      api.setFetchForTest(() => Promise.resolve(jsonResponse({
        path: '/repo', repo: '/repo', repository_identity: 'repo-a', relative_path: '',
        shallow_boundary: changedBoundary ? 'complete' : 'shallow', head: sha,
        snapshot_cursor: 'snapshot-zero', commits: [{sha}], next_cursor: '',
      })));
      assert.equal(await api.refreshGitDiffHistoryForTest(item, {refresh: true}), true);
      assert.equal(state.commits[0].files, changedBoundary ? undefined : 2, 'only unchanged Git interpretation may reuse counts');
      assert.equal(Boolean(api.gitDiffCommitRecordForTest(state, sha).detail), !changedBoundary, 'detail uses the same shallow-boundary fence');
    });
  }

  await testAsync('Git history refresh prunes old SHA caches and records a reload cursor', async () => {
    const api = loadYolomux('', ['1']);
    const item = api.gitDiffItemFor('/repo');
    const oldSha = 'a'.repeat(40), freshSha = 'b'.repeat(40), head = 'f'.repeat(40);
    const state = api.setGitDiffTabStateForTest(item, {path: '/repo', head: oldSha, commits: [{sha: oldSha}], loaded: true, loadAttempted: true});
    state.expanded.add(oldSha);
    const oldRecord = api.gitDiffCommitRecordForTest(state, oldSha);
    oldRecord.detail = {sha: oldSha};
    oldRecord.collapsedDirectories = new Set(['/repo/old']);
    api.setFetchForTest(() => Promise.resolve(jsonResponse({path: '/repo', repo: '/repo', relative_path: '', head, snapshot_cursor: 'snapshot-zero', commits: [{sha: freshSha, short: 'bbbbbbbbb'}], next_cursor: '', truncated: false})));
    assert.equal(await api.refreshGitDiffHistoryForTest(item, {refresh: true}), true);
    const refreshed = api.gitDiffTabStateForTest(item);
    assert.equal(refreshed.snapshotCursor, 'snapshot-zero', 'the first page retains an opaque offset-zero cursor for exact reload');
    assert.deepStrictEqual([...refreshed.expanded], [], 'Refresh drops disclosures outside the new bounded snapshot');
    assert.equal(refreshed.commitRecords.has(oldSha), false, 'Refresh retires the complete stale SHA record');
  });

  await testAsync('Git history fills the viewport and exposes two viewport pages without another Git request', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {bootstrapOverrides: {backendLabel: 'Python test'}, fireTimeoutDelays: [0]});
    const item = api.gitDiffItemFor('/repo');
    const commits = Array.from({length: 40}, (_, index) => ({
      sha: String(index).padStart(40, '0'), short: String(index).padStart(9, '0'), subject: `commit ${index}`,
    }));
    const requests = [];
    const dirty = deferredFetch();
    const statsStream = controllableSseResponse();
    api.setFetchForTest((url, options = {}) => {
      const parsed = new URL(String(url), 'http://localhost');
      requests.push(parsed);
      if (parsed.pathname === '/api/fs/git-history-stats-stream') {
        statsStream.bindSignal(options.signal);
        return Promise.resolve(statsStream.response);
      }
      if (parsed.searchParams.get('worktree') === 'only') {
        return dirty.promise;
      }
      return Promise.resolve(jsonResponse({path: '/repo', repo: '/repo', relative_path: '', head: 'f'.repeat(40), snapshot_cursor: 'snapshot-zero', commits, next_cursor: 'next-page', truncated: false}));
    });
    const panel = api.createGitDiffPanelForTest(item);
    api.setPanelNodeForTest(item, panel);
    Object.defineProperty(panel.querySelector('.git-diff-panel-body'), 'clientHeight', {configurable: true, value: 48});
    assert.equal(await api.refreshGitDiffHistoryForTest(item, {refresh: true}), true);
    assert.equal(panel.querySelectorAll('.git-diff-commit-row').length, 2, 'the first paint includes the available rows in the test viewport');
    assert.deepStrictEqual(requests.slice(0, 2).map(request => request.toString()), [
      'http://localhost/api/fs/git-history?path=%2Frepo&limit=2&worktree=0',
      'http://localhost/api/fs/git-history?path=%2Frepo&worktree=only',
    ], 'the short history request returns independently of the Dirty request');
    assert.equal(panel.querySelector('.git-diff-commit-changes')?.textContent, '', 'the first paint does not block on or display unavailable stats');
    assert.equal(panel.querySelector('.git-diff-commit-changes')?.getAttribute('aria-hidden'), 'true', 'unavailable stats are visually empty');
    await new Promise(resolve => setTimeout(resolve, 0));
    await flushAsyncWork();
    await flushAsyncWork();
    assert.deepStrictEqual(requests.slice(0, 3).map(request => request.toString()), [
      'http://localhost/api/fs/git-history?path=%2Frepo&limit=2&worktree=0',
      'http://localhost/api/fs/git-history?path=%2Frepo&worktree=only',
      'http://localhost/api/fs/git-history-stats-stream?path=%2Frepo&cursor=snapshot-zero&offset=0&batch_sizes=1',
    ], 'history, Dirty and the first visible SHA use separate requests');
    statsStream.push(sseFrame('start', {path: '/repo', batch_sizes: [1], offset: 0}));
    statsStream.push(sseFrame('chunk', {
      path: '/repo', repo: '/repo', relative_path: '', repository_identity: '', head: 'f'.repeat(40), snapshot_cursor: 'snapshot-zero',
      start_offset: 0, commits: [{...commits[0], files: 1, added: 2, removed: 1, binary_files: 0}], next_cursor: 'next-page', truncated: false,
    }));
    statsStream.push(sseFrame('done', {count: 1, next_cursor: 'next-page'}));
    statsStream.close();
    await flushAsyncWork();
    assert.equal(panel.querySelector('.git-diff-commit-added')?.textContent, '+2', 'stats backfill updates the painted commit row');
    assert.equal(panel.querySelector('.git-diff-commit-removed')?.textContent, '-1', 'stats backfill updates the removed count too');
    const beforeReveal = api.gitDiffTabStateForTest(item);
    assert.ok(beforeReveal.nextCursor, 'the frozen history has an older page available');
    assert.ok(beforeReveal.visibleCommitCount < beforeReveal.commits.length, 'the returned reserve contains more rows than the current viewport');
    assert.equal(api.loadOlderGitDiffHistoryForTest(item), true);
    assert.equal(panel.querySelectorAll('.git-diff-commit-row').length, 3, 'Load older paints the retained reserve immediately');
    assert.ok(api.gitDiffTabStateForTest(item).visibleCommitCount < beforeReveal.commits.length, 'one reveal leaves cached history rows available');
    assert.deepStrictEqual(requests.filter(request => request.pathname === '/api/fs/git-history' && request.searchParams.get('worktree') !== 'only').map(request => request.toString()), [
      'http://localhost/api/fs/git-history?path=%2Frepo&limit=2&worktree=0',
    ], `Load older should only reveal the returned rows; observed ${requests.map(request => request.toString()).join(' | ')}`);
    dirty.resolve(jsonResponse({path: '/repo', repository_identity: '', worktree_status: {available: true, dirty: true, entries: [{status: 'M', path: 'dirty.txt'}], files: 1, status_counts: {M: 1}}}));
    await flushAsyncWork();
    assert.equal(api.gitDiffTabStateForTest(item).worktreeStatus?.dirty, true, 'the list paints while the Dirty request is pending, then accepts its result');
  });

  await testAsync('Python backend identity enables the separate Dirty row when backendLabel is empty', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {bootstrapOverrides: {backend: 'Python 3.12.8'}});
    const item = api.gitDiffItemFor('/repo');
    const requests = [];
    api.setFetchForTest(url => {
      const parsed = new URL(String(url), 'http://localhost');
      requests.push(parsed);
      if (parsed.searchParams.get('worktree') === 'only') {
        return Promise.resolve(jsonResponse({path: '/repo', repository_identity: '', worktree_status: {
          available: true, dirty: true, entries: [{status: 'M', path: 'dirty.txt'}], files: 1, status_counts: {M: 1},
        }}));
      }
      return Promise.resolve(jsonResponse({path: '/repo', repo: '/repo', relative_path: '', head: 'f'.repeat(40),
        snapshot_cursor: 'snapshot-zero', commits: [], next_cursor: '', truncated: false}));
    });
    const panel = api.createGitDiffPanelForTest(item);
    api.setPanelNodeForTest(item, panel);
    Object.defineProperty(panel.querySelector('.git-diff-panel-body'), 'clientHeight', {configurable: true, value: 24});

    assert.equal(await api.refreshGitDiffHistoryForTest(item, {refresh: true}), true);
    await flushAsyncWork();

    assert.deepStrictEqual(requests.map(request => request.toString()), [
      'http://localhost/api/fs/git-history?path=%2Frepo&limit=1&worktree=0',
      'http://localhost/api/fs/git-history?path=%2Frepo&worktree=only',
    ], 'Python sends history and worktree status independently');
    assert.equal(panel.querySelector('.git-diff-worktree-summary')?.children[1]?.textContent, 'Dirty', 'the separate status response paints the Dirty row');
  });

  await testAsync('Python Git history progressively fetches stats for visible commits only', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {bootstrapOverrides: {backendLabel: 'Python test'}, fireTimeoutDelays: [0]});
    const item = api.gitDiffItemFor('/repo');
    const commits = Array.from({length: 25}, (_, index) => ({
      sha: String(index).padStart(40, '0'), short: String(index).padStart(9, '0'), subject: `commit ${index}`,
    }));
    const requests = [];
    const statsStream = controllableSseResponse();
    api.setFetchForTest((url, options = {}) => {
      const parsed = new URL(String(url), 'http://localhost');
      requests.push(parsed);
      if (parsed.pathname === '/api/fs/git-history-stats-stream') {
        statsStream.bindSignal(options.signal);
        return Promise.resolve(statsStream.response);
      }
      if (parsed.searchParams.get('worktree') === 'only') {
        return Promise.resolve(jsonResponse({path: '/repo', worktree_status: {available: true, dirty: false, entries: []}}));
      }
      return Promise.resolve(jsonResponse({
        path: '/repo', repo: '/repo', relative_path: '', head: 'f'.repeat(40), snapshot_cursor: 'snapshot-zero',
        commits, next_cursor: '', truncated: false,
      }));
    });
    const panel = api.createGitDiffPanelForTest(item);
    api.setPanelNodeForTest(item, panel);
    Object.defineProperty(panel.querySelector('.git-diff-panel-body'), 'clientHeight', {configurable: true, value: 48});
    assert.equal(await api.refreshGitDiffHistoryForTest(item, {refresh: true}), true);
    assert.deepStrictEqual(requests.slice(0, 2).map(request => request.toString()), [
      'http://localhost/api/fs/git-history?path=%2Frepo&limit=2&worktree=0',
      'http://localhost/api/fs/git-history?path=%2Frepo&worktree=only',
    ], 'metadata paints before any stats request');
    assert.equal(panel.querySelector('.git-diff-commit-changes').textContent, '', 'the first paint keeps stats empty');

    await new Promise(resolve => setTimeout(resolve, 0));
    await flushAsyncWork();
    const statsRequest = requests.find(request => request.pathname === '/api/fs/git-history-stats-stream');
    assert.deepStrictEqual(statsRequest && {
      batchSizes: statsRequest.searchParams.get('batch_sizes'),
      cursor: statsRequest.searchParams.get('cursor'),
      offset: statsRequest.searchParams.get('offset'),
    }, {batchSizes: '1', cursor: 'snapshot-zero', offset: '0'}, 'Python requests only the first visible SHA');
    statsStream.push(sseFrame('start', {path: '/repo', batch_sizes: [1], offset: 0}));
    statsStream.push(sseFrame('chunk', {
      path: '/repo', repo: '/repo', relative_path: '', repository_identity: '', head: 'f'.repeat(40), snapshot_cursor: 'snapshot-zero',
      start_offset: 0,
      commits: [{...commits[0], files: 1, added: 2, removed: 1, binary_files: 0}],
      next_cursor: '', truncated: false,
    }));
    statsStream.push(sseFrame('done', {count: 1, next_cursor: ''}));
    statsStream.close();
    await flushAsyncWork();
    const state = api.gitDiffTabStateForTest(item);
    assert.equal(state.commits[0].added, 2, 'the visible commit paints when its stats chunk arrives');
    assert.equal(state.commits[24].added, undefined, 'offscreen commits remain unrequested');
  });

  await testAsync('Go Git history progressively prioritizes one visible SHA before wider batches', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {bootstrapOverrides: {backendLabel: 'Go test'}, fireTimeoutDelays: [0]});
    const item = api.gitDiffItemFor('/repo');
    const commits = Array.from({length: 20}, (_, index) => ({
      sha: String(index).padStart(40, '0'), short: String(index).padStart(9, '0'), subject: `commit ${index}`,
    }));
    const requests = [];
    const streams = [controllableSseResponse(), controllableSseResponse(), controllableSseResponse()];
    let streamIndex = -1;
    api.setFetchForTest((url, options = {}) => {
      const parsed = new URL(String(url), 'http://localhost');
      requests.push(parsed);
      if (parsed.pathname.endsWith('git-history-stats-stream')) {
        streamIndex += 1;
        const stream = streams[streamIndex];
        assert.ok(stream, `unexpected stats stream ${streamIndex + 1}`);
        stream.bindSignal(options.signal);
        return Promise.resolve(stream.response);
      }
      if (parsed.searchParams.get('worktree') === 'only') {
        return Promise.resolve(jsonResponse({path: '/repo', repository_identity: 'repo-id', worktree_status: {available: true, dirty: false, entries: []}}));
      }
      return Promise.resolve(jsonResponse({path: '/repo', repo: '/repo', relative_path: '', repository_identity: 'repo-id', head: 'f'.repeat(40), snapshot_cursor: 'snapshot-zero', commits, next_cursor: '', truncated: false}));
    });
    const panel = api.createGitDiffPanelForTest(item);
    api.setPanelNodeForTest(item, panel);
    Object.defineProperty(panel.querySelector('.git-diff-panel-body'), 'clientHeight', {configurable: true, value: 48});
    assert.equal(await api.refreshGitDiffHistoryForTest(item, {refresh: true}), true);
    await new Promise(resolve => setTimeout(resolve, 0));
    await flushAsyncWork();
    assert.deepStrictEqual(requests.filter(request => request.searchParams.get('worktree') !== 'only').map(request => ({path: request.pathname, limit: request.searchParams.get('limit'), batchSizes: request.searchParams.get('batch_sizes'), cursor: request.searchParams.get('cursor')})), [
      {path: '/api/fs/git-history', limit: '2', batchSizes: null, cursor: null},
      {path: '/api/fs/git-history-stats-stream', limit: null, batchSizes: '1', cursor: 'snapshot-zero'},
    ], 'the first stats request asks for only the top SHA');

    const streamPayload = (offset, size, nextCursor) => ({
      path: '/repo', repo: '/repo', relative_path: '', repository_identity: 'repo-id', head: 'f'.repeat(40), snapshot_cursor: 'snapshot-zero',
      start_offset: offset,
      commits: commits.slice(offset, offset + size).map(commit => ({...commit, files: 1, added: offset + 1, removed: 1, binary_files: 0, signature_status: 'N'})), next_cursor: nextCursor, truncated: false,
    });
    streams[0].push(sseFrame('start', {path: '/repo', batch_sizes: [1]}));
    streams[0].push(sseFrame('chunk', streamPayload(0, 1, 'cursor-1')));
    await flushAsyncWork();
    assert.equal(api.gitDiffTabStateForTest(item).commits[0].added, 1, 'the top SHA paints as soon as its chunk arrives');
    streams[0].push(sseFrame('done', {count: 1, next_cursor: 'cursor-1'}));
    streams[0].close();
    await flushAsyncWork();
    await new Promise(resolve => setTimeout(resolve, 0));
    await flushAsyncWork();
    assert.deepStrictEqual(requests.filter(request => request.pathname.endsWith('stats-stream')).map(request => ({batchSizes: request.searchParams.get('batch_sizes'), cursor: request.searchParams.get('cursor')})), [
      {batchSizes: '1', cursor: 'snapshot-zero'},
      {batchSizes: '1,2', cursor: 'snapshot-zero'},
    ], 'the next request widens to one plus two SHAs within the same snapshot');
    streams[1].push(sseFrame('start', {path: '/repo', batch_sizes: [1, 2]}));
    streams[1].push(sseFrame('chunk', streamPayload(2, 2, 'cursor-3')));
    await flushAsyncWork();
    assert.equal(api.gitDiffTabStateForTest(item).commits[2].added, 3, 'the later two-SHA chunk paints as soon as it arrives');
    streams[1].push(sseFrame('chunk', streamPayload(1, 1, 'cursor-2')));
    streams[1].push(sseFrame('done', {count: 3, next_cursor: 'cursor-3'}));
    streams[1].close();
    await flushAsyncWork();
    const body = panel.querySelector('.git-diff-panel-body');
    body.clientHeight = 456;
    panel.querySelector('.git-diff-commit-row').rect = {left: 0, right: 500, top: 0, bottom: 24, width: 500, height: 24};
    assert.equal(api.loadOlderGitDiffHistoryForTest(item), true, 'expanding the visible window admits the next stats stage');
    await new Promise(resolve => setTimeout(resolve, 0));
    await flushAsyncWork();
    assert.deepStrictEqual(requests.filter(request => request.pathname.endsWith('stats-stream')).map(request => ({batchSizes: request.searchParams.get('batch_sizes'), cursor: request.searchParams.get('cursor')})), [
      {batchSizes: '1', cursor: 'snapshot-zero'},
      {batchSizes: '1,2', cursor: 'snapshot-zero'},
      {batchSizes: '4,4,4,4', cursor: 'snapshot-zero'},
    ], 'later requests use four-SHA batches within the same snapshot');
    streams[2].push(sseFrame('start', {path: '/repo', batch_sizes: [4, 4, 4, 4]}));
    streams[2].push(sseFrame('chunk', streamPayload(12, 4, 'cursor-16')));
    await flushAsyncWork();
    assert.equal(api.gitDiffTabStateForTest(item).commits[15].added, 13, 'a later four-SHA chunk paints before earlier chunks');
    streams[2].push(sseFrame('chunk', streamPayload(4, 4, 'cursor-8')));
    streams[2].push(sseFrame('chunk', streamPayload(8, 4, 'cursor-12')));
    streams[2].push(sseFrame('chunk', streamPayload(16, 4, '')));
    streams[2].push(sseFrame('done', {count: 16, next_cursor: ''}));
    streams[2].close();
    await flushAsyncWork();
    await flushAsyncWork();
    const state = api.gitDiffTabStateForTest(item);
    assert.ok(state.commits.every(commit => Number.isSafeInteger(commit.added) && Number.isSafeInteger(commit.removed) && Number.isSafeInteger(commit.files) && Number.isSafeInteger(commit.binary_files)), 'every commit on the loaded page has complete stats');
    assert.equal(state.statsStage, 3, 'the progressive schedule completed the one, one-plus-two, and four-SHA stages');
  });

  await testAsync('Go cached history counts with deferred signature status still request SHA enrichment', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {bootstrapOverrides: {backendLabel: 'Go test'}, fireTimeoutDelays: [0]});
    const item = api.gitDiffItemFor('/repo');
    const sha = 'a'.repeat(40);
    const requests = [];
    const stream = controllableSseResponse();
    api.setFetchForTest((url, options = {}) => {
      const parsed = new URL(String(url), 'http://localhost');
      requests.push(parsed);
      if (parsed.pathname.endsWith('git-history-stats-stream')) {
        stream.bindSignal(options.signal);
        return Promise.resolve(stream.response);
      }
      if (parsed.searchParams.get('worktree') === 'only') {
        return Promise.resolve(jsonResponse({path: '/repo', repository_identity: 'repo-id', worktree_status: {available: true, dirty: false, entries: []}}));
      }
      return Promise.resolve(jsonResponse({
        path: '/repo', repo: '/repo', relative_path: '', repository_identity: 'repo-id', head: sha, snapshot_cursor: 'snapshot-zero',
        commits: [{sha, files: 1, added: 1, removed: 0, binary_files: 0, signature_status: ''}], next_cursor: '', truncated: false,
      }));
    });
    const panel = api.createGitDiffPanelForTest(item);
    api.setPanelNodeForTest(item, panel);
    assert.equal(await api.refreshGitDiffHistoryForTest(item, {refresh: true}), true);
    await new Promise(resolve => setTimeout(resolve, 0));
    await flushAsyncWork();
    assert.equal(requests.filter(request => request.pathname.endsWith('git-history-stats-stream')).length, 1,
      'empty Go signature status keeps the visible row eligible for SHA enrichment');
    stream.push(sseFrame('start', {path: '/repo', batch_sizes: [1]}));
    stream.push(sseFrame('chunk', {
      path: '/repo', repo: '/repo', relative_path: '', repository_identity: 'repo-id', head: sha, snapshot_cursor: 'snapshot-zero', start_offset: 0,
      commits: [{sha, files: 1, added: 1, removed: 0, binary_files: 0, signature_status: 'N'}], next_cursor: '', truncated: false,
    }));
    stream.push(sseFrame('done', {count: 1, next_cursor: ''}));
    stream.close();
    await flushAsyncWork();
    assert.equal(api.gitDiffTabStateForTest(item).commits[0].signature_status, 'N', 'the refreshed signature status reaches the row');
  });

  await testAsync('Git history refresh cancels an in-flight stats backfill before applying stale counts', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {bootstrapOverrides: {backendLabel: 'Go test'}, fireTimeoutDelays: [0]});
    const item = api.gitDiffItemFor('/repo');
    const sha = 'a'.repeat(40);
    const statsStream = controllableSseResponse();
    const statsSignals = [];
    api.setFetchForTest((url, options = {}) => {
      const parsed = new URL(String(url), 'http://localhost');
      if (parsed.pathname.endsWith('git-history-stats-stream')) {
        statsSignals.push(options.signal);
        statsStream.bindSignal(options.signal);
        return Promise.resolve(statsStream.response);
      }
      return Promise.resolve(jsonResponse({
        path: '/repo', repo: '/repo', relative_path: '', head: 'f'.repeat(40), snapshot_cursor: 'snapshot-zero',
        commits: [{sha, short: 'aaaaaaaaa', subject: 'fresh'}], next_cursor: '', truncated: false,
      }));
    });
    const panel = api.createGitDiffPanelForTest(item);
    api.setPanelNodeForTest(item, panel);
    await api.refreshGitDiffHistoryForTest(item, {refresh: true});
    await new Promise(resolve => setTimeout(resolve, 0));
    await flushAsyncWork();
    assert.equal(api.gitDiffTabStateForTest(item).statsLoading, true, 'the first stats request is in flight');

    api.setFetchForTest(url => {
      const parsed = new URL(String(url), 'http://localhost');
      assert.equal(parsed.pathname, '/api/fs/git-history', 'refresh does not wait for stale stats before fetching metadata');
      return Promise.resolve(jsonResponse({
        path: '/repo', repo: '/repo', relative_path: '', head: 'f'.repeat(40), snapshot_cursor: 'fresh-snapshot',
        commits: [{sha, short: 'aaaaaaaaa', subject: 'fresh', files: 1, added: 7, removed: 0, binary_files: 0}], next_cursor: '', truncated: false,
      }));
    });
    assert.equal(await api.refreshGitDiffHistoryForTest(item, {refresh: true}), true);
    statsStream.push(sseFrame('chunk', {
      path: '/repo', repo: '/repo', relative_path: '', head: 'f'.repeat(40), snapshot_cursor: 'snapshot-zero',
      commits: [{sha, short: 'aaaaaaaaa', subject: 'stale', files: 1, added: 99, removed: 99, binary_files: 0}], next_cursor: '', truncated: false,
    }));
    statsStream.close();
    await flushAsyncWork();
    assert.equal(statsSignals.length, 1, 'the Go first window has one in-flight stream');
    assert.ok(statsSignals[0]?.aborted, 'refresh aborts the old stats stream');
    assert.equal(api.gitDiffTabStateForTest(item).commits[0].added, 7, 'stale stats cannot overwrite refreshed metadata');
  });

  await testAsync('Git history auto-loads when the initial rows do not create a scrollbar', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {bootstrapOverrides: {backendLabel: 'Python test'}});
    const item = api.gitDiffItemFor('/repo');
    const commits = Array.from({length: 8}, (_, index) => ({
      sha: String(index).padStart(40, '0'), short: String(index).padStart(9, '0'), subject: `commit ${index}`,
    }));
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      const stats = String(url).includes('stats=1');
      const historyRequestCount = requests.filter(request => !request.includes('stats=1')).length;
      return Promise.resolve(jsonResponse({path: '/repo', repo: '/repo', relative_path: '', head: 'f'.repeat(40), snapshot_cursor: 'snapshot-zero', commits: stats ? commits.map(commit => ({...commit, files: 1, added: 2, removed: 1, binary_files: 0})) : commits, next_cursor: stats ? '' : historyRequestCount === 1 ? 'next-page' : '', truncated: false}));
    });
    const panel = api.createGitDiffPanelForTest(item);
    api.setPanelNodeForTest(item, panel);
    const body = panel.querySelector('.git-diff-panel-body');
    Object.defineProperties(body, {
      clientHeight: {configurable: true, value: 480},
      scrollHeight: {configurable: true, get: () => Math.max(1, body.querySelectorAll('.git-diff-commit-row').length * 24)},
    });
    await api.refreshGitDiffHistoryForTest(item, {refresh: true});
    await new Promise(resolve => setTimeout(resolve, 0));
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(requests.filter(request => !request.includes('stats=1') && !request.includes('worktree=only')).length, 2, 'a short initial list requests its next page without waiting for an impossible scroll event');
    assert.equal(panel.querySelectorAll('.git-diff-commit-row').length, 8, 'auto-loaded history renders the complete available result');
  });

  await testAsync('Git history freezes pagination and fences stale refresh generations without dropping valid rows', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {bootstrapOverrides: {backendLabel: 'Python test'}});
    const item = api.gitDiffItemFor('/repo/src');
    const stale = deferredFetch();
    const fresh = deferredFetch();
    const older = deferredFetch();
    const requests = [];
    api.setFetchForTest((url, options = {}) => {
      requests.push({url: String(url), signal: options.signal});
      const parsed = new URL(String(url), 'http://localhost');
      if (parsed.searchParams.get('stats') === '1') return Promise.resolve(jsonResponse({path: '/repo/src', repo: '/repo', relative_path: 'src', head: 'f'.repeat(40), commits: [{sha: 'f'.repeat(40), short: 'fffffffff', subject: 'fresh', files: 1, added: 1, removed: 0, binary_files: 0}], next_cursor: '', truncated: false}));
      const historyRequests = requests.filter(request => !request.url.includes('stats=1')).length;
      if (historyRequests === 1) return stale.promise;
      if (historyRequests === 2) return fresh.promise;
      return older.promise;
    });
    const staleLoad = api.refreshGitDiffHistoryForTest(item, {refresh: true});
    const freshLoad = api.refreshGitDiffHistoryForTest(item, {refresh: true});
    fresh.resolve(jsonResponse({path: '/repo/src', repo: '/repo', relative_path: 'src', head: 'f'.repeat(40), commits: [{sha: 'f'.repeat(40), short: 'fffffffff', subject: 'fresh'}], next_cursor: 'frozen-cursor', truncated: false}));
    await freshLoad;
    stale.resolve(jsonResponse({path: '/repo/src', repo: '/repo', relative_path: 'src', head: 'e'.repeat(40), commits: [{sha: 'e'.repeat(40), short: 'eeeeeeeee', subject: 'stale'}], next_cursor: '', truncated: false}));
    await staleLoad;
    let state = api.gitDiffTabStateForTest(item);
    assert.equal(state.head, 'f'.repeat(40), 'late refresh data cannot replace the newer generation');
    assert.deepStrictEqual([...state.commits.map(commit => commit.subject)], ['fresh']);
    assert.match(requests.find(request => !request.url.includes('stats=1') && !request.url.includes('worktree=only')).url, /^\/api\/fs\/git-history\?path=%2Frepo%2Fsrc&limit=1&worktree=0$/);

    const append = api.loadOlderGitDiffHistoryForTest(item);
    assert.ok(requests.find(request => request.url.includes('cursor=frozen-cursor')).url, 'pagination uses the frozen snapshot cursor');
    older.resolve(jsonResponse({path: '/repo/src', repo: '/repo', relative_path: 'src', head: 'f'.repeat(40), commits: [{sha: 'd'.repeat(40), short: 'ddddddddd', subject: 'older'}], next_cursor: '', truncated: true, truncation_reason: 'cursor_limit'}));
    await append;
    state = api.gitDiffTabStateForTest(item);
    assert.deepStrictEqual([...state.commits.map(commit => commit.subject)], ['fresh', 'older']);
    assert.equal(state.truncated, true, 'bounded pagination remains visibly partial');

    api.setFetchForTest(() => Promise.reject(new Error('offline')));
    assert.equal(await api.refreshGitDiffHistoryForTest(item, {refresh: true}), false);
    state = api.gitDiffTabStateForTest(item);
    assert.deepStrictEqual([...state.commits.map(commit => commit.subject)], ['fresh', 'older'], 'refresh failure retains the prior valid snapshot');
    assert.ok(state.error, 'refresh failure is presented instead of silently clearing the list');
  });

  await testAsync('Git history retries once without a stale saved cursor', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {bootstrapOverrides: {backendLabel: 'Python test'}});
    const item = api.gitDiffItemFor('/repo');
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      if (requests.length === 1) {
        const error = new Error('invalid Git history cursor');
        error.status = 400;
        error.payload = {user_message: {key: 'fs.error.gitHistoryCursor', params: {}, fallback: 'invalid Git history cursor'}};
        return Promise.reject(error);
      }
      return Promise.resolve(jsonResponse({
        path: '/repo', repo: '/repo', relative_path: '', head: 'f'.repeat(40),
        snapshot_cursor: 'fresh-cursor', commits: [], next_cursor: '', truncated: false,
      }));
    });
    api.setGitDiffTabStateForTest(item, {snapshotCursor: 'old-cursor'});
    assert.equal(await api.refreshGitDiffHistoryForTest(item), true);
    assert.deepStrictEqual(requests, [
      '/api/fs/git-history?path=%2Frepo&limit=1&cursor=old-cursor&worktree=0',
      '/api/fs/git-history?path=%2Frepo&limit=1&worktree=0',
      '/api/fs/git-history?path=%2Frepo&worktree=only',
    ]);
    assert.equal(api.gitDiffTabStateForTest(item).snapshotCursor, 'fresh-cursor');
  });

  await testAsync('Git history drops a saved cursor when its snapshot is stale', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {bootstrapOverrides: {backendLabel: 'Python test'}});
    const item = api.gitDiffItemFor('/repo');
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      if (requests.length === 1) {
        const error = new Error('Git history snapshot is stale');
        error.status = 409;
        error.payload = {user_message: {key: 'fs.error.gitHistoryStale', params: {}, fallback: error.message}};
        return Promise.reject(error);
      }
      return Promise.resolve(jsonResponse({
        path: '/repo', repo: '/repo', relative_path: '', head: 'f'.repeat(40),
        snapshot_cursor: 'fresh-cursor', commits: [], next_cursor: '', truncated: false,
      }));
    });
    api.setGitDiffTabStateForTest(item, {snapshotCursor: 'rewritten-history-cursor'});
    assert.equal(await api.refreshGitDiffHistoryForTest(item), true);
    assert.deepStrictEqual(requests, [
      '/api/fs/git-history?path=%2Frepo&limit=1&cursor=rewritten-history-cursor&worktree=0',
      '/api/fs/git-history?path=%2Frepo&limit=1&worktree=0',
      '/api/fs/git-history?path=%2Frepo&worktree=only',
    ]);
    assert.equal(api.gitDiffTabStateForTest(item).snapshotCursor, 'fresh-cursor');
  });

  await testAsync('Git history stops after one cursor-free retry when the new snapshot is also stale', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {bootstrapOverrides: {backendLabel: 'Go test'}});
    const item = api.gitDiffItemFor('/repo');
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      const error = new Error('Git history snapshot is stale');
      error.status = 409;
      error.code = 'git_history_stale';
      error.payload = {
        error_code: 'git_history_stale', message_key: 'fs.error.gitHistoryStale',
        user_message: {key: 'fs.error.gitHistoryStale', params: {}, fallback: error.message},
      };
      return Promise.reject(error);
    });
    const state = api.setGitDiffTabStateForTest(item, {snapshotCursor: 'rewritten-history-cursor'});
    assert.equal(await api.refreshGitDiffHistoryForTest(item), false);
    assert.deepStrictEqual(requests, [
      '/api/fs/git-history?path=%2Frepo&limit=1&cursor=rewritten-history-cursor&worktree=0',
      '/api/fs/git-history?path=%2Frepo&limit=1&worktree=0',
    ]);
    assert.equal(state.snapshotCursor, '');
    assert.equal(state.loading, false);
    assert.equal(state.error?.user_message?.key, 'fs.error.gitHistoryStale');
  });

  await testAsync('Git commit detail refreshes history once when its frozen head is stale', async () => {
    const api = loadYolomux('', ['1']);
    const item = api.gitDiffItemFor('/repo');
    const sha = 'a'.repeat(40);
    const head = 'b'.repeat(40);
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      if (requests.length === 1) {
        const error = new Error('Git history snapshot is stale');
        error.status = 409;
        error.payload = {user_message: {key: 'fs.error.gitHistoryStale', params: {}, fallback: error.message}};
        return Promise.reject(error);
      }
      if (requests.length === 2) {
        return Promise.resolve(jsonResponse({path: '/repo', repo: '/repo', relative_path: '', head, commits: [{sha, short: 'aaaaaaaaa', subject: 'fresh', files: 1, added: 1, removed: 0, binary_files: 0}], next_cursor: '', truncated: false}));
      }
      return Promise.resolve(jsonResponse({sha, repo: '/repo', parents: [], from_ref: '0'.repeat(40), to_ref: sha, message: 'fresh detail', files: [], message_truncated: false, files_truncated: false, truncated: false}));
    });
    const state = api.setGitDiffTabStateForTest(item, {path: '/repo', head, commits: [{sha, short: 'aaaaaaaaa', subject: 'fresh'}], visibleCommitCount: 1, loaded: true, loadAttempted: true});
    state.expanded.add(sha);
    const panel = installVisibleGitDiffPanel(api, item);
    const body = panel.querySelector('.git-diff-panel-body');
    body.clientHeight = 100;
    body.rect = {width: 1200, height: 100, left: 0, top: 0, right: 1200, bottom: 100};
    panel.querySelector('.git-diff-commit[data-git-diff-commit-group]').rect = {width: 1200, height: 20, left: 0, top: 10, right: 1200, bottom: 30};
    const detailResult = await api.loadGitDiffCommitDetailForTest(item, sha);
    assert.equal(detailResult, true, `visible SHA detail retry result; requests=${JSON.stringify(requests)}`);
    assert.deepStrictEqual(requests, [
      `/api/fs/git-commit?path=%2Frepo&commit=${sha}&head=${head}`,
      '/api/fs/git-history?path=%2Frepo&limit=1',
      `/api/fs/git-commit?path=%2Frepo&commit=${sha}&head=${head}`,
    ]);
    assert.equal(api.gitDiffCommitRecordForTest(api.gitDiffTabStateForTest(item), sha).error, null);
  });

  await testAsync('Git commit stale-detail retry stops when its row scrolls offscreen during history refresh', async () => {
    const api = loadYolomux('', ['1']);
    const item = api.gitDiffItemFor('/repo');
    const sha = 'a'.repeat(40);
    const head = 'b'.repeat(40);
    const history = deferredFetch();
    const requests = [];
    api.setGitDiffTabStateForTest(item, {
      path: '/repo', head, commits: [{sha, short: 'aaaaaaaaa', subject: 'commit'}],
      visibleCommitCount: 1, loaded: true, loadAttempted: true,
    }).expanded.add(sha);
    const panel = installVisibleGitDiffPanel(api, item);
    const body = panel.querySelector('.git-diff-panel-body');
    body.clientHeight = 100;
    body.rect = {width: 1200, height: 100, left: 0, top: 0, right: 1200, bottom: 100};
    let rowOffscreen = false;
    const querySelectorAll = body.querySelectorAll.bind(body);
    body.querySelectorAll = selector => {
      const rows = querySelectorAll(selector);
      if (selector === '.git-diff-commit[data-git-diff-commit-group]') {
        for (const row of rows) {
          row.rect = rowOffscreen
            ? {width: 1200, height: 20, left: 0, top: -130, right: 1200, bottom: -110}
            : {width: 1200, height: 20, left: 0, top: 10, right: 1200, bottom: 30};
        }
      }
      return rows;
    };
    api.setFetchForTest(url => {
      const parsed = new URL(String(url), 'http://localhost');
      requests.push(parsed.pathname);
      if (parsed.pathname === '/api/fs/git-commit') {
        const error = new Error('Git history snapshot is stale');
        error.status = 409;
        error.payload = {user_message: {key: 'fs.error.gitHistoryStale', params: {}, fallback: error.message}};
        return Promise.reject(error);
      }
      assert.equal(parsed.pathname, '/api/fs/git-history');
      return history.promise;
    });

    const detailRequest = api.loadGitDiffCommitDetailForTest(item, sha);
    await flushAsyncWork();
    assert.deepStrictEqual(requests, ['/api/fs/git-commit', '/api/fs/git-history'], 'the stale response starts one frozen-history refresh');
    rowOffscreen = true;
    assert.equal(api.backfillGitDiffVisibleCommitDetailsForTest(item), false, 'the test viewport no longer contains the expanded SHA');

    history.resolve(jsonResponse({
      path: '/repo', repo: '/repo', relative_path: '', head, snapshot_cursor: 'fresh-snapshot',
      commits: [{sha, short: 'aaaaaaaaa', subject: 'fresh', files: 1, added: 1, removed: 0, binary_files: 0}],
      next_cursor: '', truncated: false,
    }));
    assert.equal(await detailRequest, false, 'the offscreen stale request does not retry');
    await flushAsyncWork();
    assert.deepStrictEqual(requests, ['/api/fs/git-commit', '/api/fs/git-history'], 'no commit-detail request starts after the refresh');
    assert.equal(api.gitDiffCommitRecordForTest(api.gitDiffTabStateForTest(item), sha).detail, null, 'offscreen detail stays uncached until the row is visible again');
  });

  for (const boundaryChange of [false, true]) {
  await testAsync(`Git commit refresh replaces delayed detail work when ${boundaryChange ? 'shallow boundary' : 'repository identity'} changes`, async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {bootstrapOverrides: {backendLabel: 'Python test'}});
    const item = api.gitDiffItemFor('/repo/src');
    const sha = 'a'.repeat(40);
    const head = 'b'.repeat(40);
    const history = deferredFetch();
    const oldDetail = deferredFetch();
    const currentDetail = deferredFetch();
    let detailRequestCount = 0;
    const state = api.setGitDiffTabStateForTest(item, {
      path: '/repo/src', repo: '/repo', relativePath: 'src', repositoryIdentity: 'repo-a', shallowBoundary: 'old-boundary',
      head, commits: [{sha}], loaded: true, loadAttempted: true,
    });
    state.expanded.add(sha);
    installVisibleGitDiffPanel(api, item);
    api.setFetchForTest(url => {
      const pathname = new URL(String(url), 'http://localhost').pathname;
      if (pathname === '/api/fs/git-history') return history.promise;
      detailRequestCount += 1;
      return detailRequestCount === 1 ? oldDetail.promise : currentDetail.promise;
    });

    const refresh = api.refreshGitDiffHistoryForTest(item, {refresh: true});
    await flushAsyncWork();
    const oldRequest = api.loadGitDiffCommitDetailForTest(item, sha);
    await flushAsyncWork();
    history.resolve(jsonResponse({
      path: '/repo/src', repo: '/repo', relative_path: 'src', repository_identity: boundaryChange ? 'repo-a' : 'repo-b', shallow_boundary: boundaryChange ? 'new-boundary' : 'old-boundary',
      head, snapshot_cursor: 'snapshot-repo-b', commits: [{sha}], next_cursor: '', truncated: false,
    }));
    assert.equal(await refresh, true, 'the replacement repository history becomes current');
    await flushAsyncWork();
    assert.equal(detailRequestCount, 2, 'the expanded SHA starts a replacement request for the new identity');

    oldDetail.resolve(jsonResponse({
      repo: '/repo', sha, parents: [], from_ref: '0'.repeat(40), to_ref: sha,
      shallow_boundary: 'old-boundary', message: 'old repository detail', files: [], message_truncated: false,
      files_truncated: false, truncated: false,
    }));

    await oldRequest;
    await flushAsyncWork();
    assert.equal(state.commitRecords.get(sha)?.detail, null, 'the old repository detail is never cached under the new identity');
    const currentRequest = state.commitRecords.get(sha)?.loading;
    assert.ok(currentRequest, 'the replacement detail request remains in flight');
    currentDetail.resolve(jsonResponse({
      repo: '/repo', sha, parents: [], from_ref: '0'.repeat(40), to_ref: sha,
      shallow_boundary: boundaryChange ? 'new-boundary' : 'old-boundary', message: 'current repository detail', files: [{path: 'src/current.go'}], message_truncated: false,
      files_truncated: false, truncated: false,
    }));
    await currentRequest;
    assert.equal(state.commitRecords.get(sha)?.detail?.message, 'current repository detail', 'the replacement publishes the actual loaded detail');
    assert.deepStrictEqual(state.commitRecords.get(sha)?.detail?.files.map(file => file.path), ['src/current.go']);
  });
  }

  await testAsync('Git commit detail loading follows the visible expanded rows', async () => {
    const api = loadYolomux('', ['1']);
    const item = api.gitDiffItemFor('/repo');
    const shaA = 'a'.repeat(40);
    const shaB = 'b'.repeat(40);
    const head = 'f'.repeat(40);
    const detailA = deferredFetch();
    const detailB = deferredFetch();
    const requests = [];
    const state = api.setGitDiffTabStateForTest(item, {
      path: '/repo', shallowBoundary: 'complete', head, commits: [{sha: shaA}, {sha: shaB}],
      visibleCommitCount: 2, loaded: true, loadAttempted: true,
    });
    state.expanded.add(shaA);
    state.expanded.add(shaB);
    const panel = api.createGitDiffPanelForTest(item);
    api.setPanelNodeForTest(item, panel);
    api.renderGitDiffPanelForTest(item, {panel});
    const body = panel.querySelector('.git-diff-panel-body');
    body.clientHeight = 100;
    body.rect = {width: 1200, height: 100, left: 0, top: 0, right: 1200, bottom: 100};
    const setTop = (sha, top) => {
      const group = body.querySelectorAll('.git-diff-commit[data-git-diff-commit-group]')
        .find(node => node.dataset.gitDiffCommitGroup === sha);
      group.rect = {width: 1200, height: 20, left: 0, top, right: 1200, bottom: top + 20};
    };
    setTop(shaA, 10);
    setTop(shaB, 120);
    api.setFetchForTest((url, options) => {
      const sha = new URL(String(url), 'http://localhost').searchParams.get('commit');
      requests.push({sha, signal: options?.signal});
      return sha === shaA ? detailA.promise : detailB.promise;
    });

    api.backfillGitDiffVisibleCommitDetailsForTest(item);
    await flushAsyncWork();
    assert.deepStrictEqual(requests.map(request => request.sha), [shaA], 'an expanded SHA below the viewport waits');
    const staleRequest = state.commitRecords.get(shaA).loading;

    setTop(shaA, -130);
    setTop(shaB, 10);
    api.backfillGitDiffVisibleCommitDetailsForTest(item);
    await flushAsyncWork();
    assert.deepStrictEqual(requests.map(request => request.sha), [shaA, shaB], 'scrolling starts the visible SHA detail');
    assert.equal(requests[0].signal.aborted, true, 'scrolling away cancels the obsolete detail request');
    assert.equal(state.commitRecords.get(shaA).loading, null);

    detailA.resolve(jsonResponse({
      repo: '/repo', sha: shaA, parents: [], from_ref: '0'.repeat(40), to_ref: shaA,
      message: 'offscreen detail', files: [], message_truncated: false, files_truncated: false, truncated: false,
    }));
    assert.equal(await staleRequest, false, 'the offscreen response is discarded');
    assert.equal(state.commitRecords.get(shaA).detail, null);

    const visibleRequest = state.commitRecords.get(shaB).loading;
    detailB.resolve(jsonResponse({
      repo: '/repo', sha: shaB, parents: [], from_ref: '0'.repeat(40), to_ref: shaB,
      message: 'visible detail', files: [], message_truncated: false, files_truncated: false, truncated: false,
    }));
    assert.equal(await visibleRequest, true);
    assert.equal(state.commitRecords.get(shaB).detail?.message, 'visible detail');
  });

  await testAsync('Git commit identity refinement retires in-flight detail while preserving disclosure state', async () => {
    const api = loadYolomux('', ['1']);
    const item = api.gitDiffItemFor('/repo/src');
    const sha = 'c'.repeat(40);
    const head = 'd'.repeat(40);
    const history = deferredFetch();
    const oldDetail = deferredFetch();
    const currentDetail = deferredFetch();
    let detailRequestCount = 0;
    const state = api.setGitDiffTabStateForTest(item, {
      path: '/repo/src', repo: '/repo', relativePath: 'src', head,
      commits: [{sha}], loaded: true, loadAttempted: true,
    });
    state.expanded.add(sha);
    state.focusedSha = sha;
    installVisibleGitDiffPanel(api, item);
    const record = api.gitDiffCommitRecordForTest(state, sha);
    record.collapsedDirectories = new Set(['/repo/src/lib']);
    api.setFetchForTest(url => {
      const pathname = new URL(String(url), 'http://localhost').pathname;
      if (pathname === '/api/fs/git-history') return history.promise;
      detailRequestCount += 1;
      return detailRequestCount === 1 ? oldDetail.promise : currentDetail.promise;
    });

    const refresh = api.refreshGitDiffHistoryForTest(item, {refresh: true});
    await flushAsyncWork();
    const oldRequest = api.loadGitDiffCommitDetailForTest(item, sha);
    await flushAsyncWork();
    history.resolve(jsonResponse({
      path: '/repo/src', repo: '/repo', relative_path: 'src', repository_identity: 'repo-b',
      head, commits: [{sha}], next_cursor: '', truncated: false,
    }));
    assert.equal(await refresh, true);
    await flushAsyncWork();
    assert.equal(detailRequestCount, 2, 'identity refinement starts a replacement instead of waiting for obsolete detail');
    assert.deepStrictEqual([...state.commitRecords.get(sha).collapsedDirectories], ['/repo/src/lib']);
    assert.deepStrictEqual([state.focusedSha, state.expanded.has(sha)], [sha, true]);

    oldDetail.resolve(jsonResponse({
      repo: '/repo', sha, parents: [], from_ref: '0'.repeat(40), to_ref: sha,
      message: 'obsolete', files: [], message_truncated: false,
      files_truncated: false, truncated: false,
    }));
    await oldRequest;
    await flushAsyncWork();
    assert.equal(state.commitRecords.get(sha)?.detail, null, 'the obsolete payload is discarded');
    assert.deepStrictEqual([...state.commitRecords.get(sha).collapsedDirectories], ['/repo/src/lib'], 'settling obsolete work cannot delete disclosure state');
    assert.ok(state.commitRecords.get(sha)?.loading, 'the replacement request remains active');

    currentDetail.resolve(jsonResponse({
      repo: '/repo', sha, parents: [], from_ref: '0'.repeat(40), to_ref: sha,
      message: 'current', files: [], message_truncated: false,
      files_truncated: false, truncated: false,
    }));
    const replacement = state.commitRecords.get(sha).loading;
    await replacement;
    assert.equal(state.commitRecords.get(sha)?.detail?.message, 'current');
    assert.deepStrictEqual([...state.commitRecords.get(sha).collapsedDirectories], ['/repo/src/lib']);
  });

  await testAsync('Git commit HEAD advance replaces detail work started during history refresh', async () => {
    const api = loadYolomux('', ['1']);
    const item = api.gitDiffItemFor('/repo/src');
    const sha = 'e'.repeat(40);
    const oldHead = 'f'.repeat(40);
    const newHead = '1'.repeat(40);
    const history = deferredFetch();
    const oldDetail = deferredFetch();
    const currentDetail = deferredFetch();
    let detailRequestCount = 0;
    const state = api.setGitDiffTabStateForTest(item, {
      path: '/repo/src', repo: '/repo', relativePath: 'src', repositoryIdentity: 'repo-a',
      head: oldHead, commits: [{sha}], loaded: true, loadAttempted: true,
    });
    state.expanded.add(sha);
    installVisibleGitDiffPanel(api, item);
    const record = api.gitDiffCommitRecordForTest(state, sha);
    record.collapsedDirectories = new Set(['/repo/src/lib']);
    api.setFetchForTest(url => {
      const pathname = new URL(String(url), 'http://localhost').pathname;
      if (pathname === '/api/fs/git-history') return history.promise;
      detailRequestCount += 1;
      return detailRequestCount === 1 ? oldDetail.promise : currentDetail.promise;
    });

    const refresh = api.refreshGitDiffHistoryForTest(item, {refresh: true});
    await flushAsyncWork();
    const oldRequest = api.loadGitDiffCommitDetailForTest(item, sha);
    await flushAsyncWork();
    history.resolve(jsonResponse({
      path: '/repo/src', repo: '/repo', relative_path: 'src', repository_identity: 'repo-a',
      head: newHead, commits: [{sha}], next_cursor: '', truncated: false,
    }));
    assert.equal(await refresh, true);
    await flushAsyncWork();
    assert.equal(detailRequestCount, 2, 'the accepted new HEAD starts a replacement detail request');
    assert.deepStrictEqual([...state.commitRecords.get(sha).collapsedDirectories], ['/repo/src/lib']);

    oldDetail.resolve(jsonResponse({
      repo: '/repo', sha, parents: [], from_ref: '0'.repeat(40), to_ref: sha,
      message: 'old snapshot', files: [], message_truncated: false,
      files_truncated: false, truncated: false,
    }));
    await oldRequest;
    await flushAsyncWork();
    assert.equal(state.commitRecords.get(sha)?.detail, null, 'detail requested against the old HEAD is discarded');
    const replacement = state.commitRecords.get(sha)?.loading;
    assert.ok(replacement, 'the replacement remains in flight after the obsolete request settles');
    assert.deepStrictEqual([...state.commitRecords.get(sha).collapsedDirectories], ['/repo/src/lib']);

    currentDetail.resolve(jsonResponse({
      repo: '/repo', sha, parents: [], from_ref: '0'.repeat(40), to_ref: sha,
      message: 'new snapshot', files: [], message_truncated: false,
      files_truncated: false, truncated: false,
    }));
    await replacement;
    assert.equal(state.commitRecords.get(sha)?.detail?.message, 'new snapshot');
  });

  test('Git commit messages render escaped newline markers as actual returns', () => {
    const api = loadYolomux('', ['1']);
    const message = api.gitDiffCommitMessageForTest({message: 'subject\\n\\nbody'});
    assert.equal(message.textContent, 'subject\n\nbody');
  });

  await testAsync('Git commit disclosures load independently and changed files retain status, rename, binary, and exact refs', async () => {
    const api = loadYolomux('', ['1']);
    const item = api.gitDiffItemFor('/repo');
    const shaA = 'a'.repeat(40);
    const shaB = 'b'.repeat(40);
    const head = 'f'.repeat(40);
    api.setGitDiffTabStateForTest(item, {path: '/repo', repo: '/repo', relativePath: '', head, commits: [{sha: shaA, short: 'aaaaaaaaa'}, {sha: shaB, short: 'bbbbbbbbb'}], loaded: true});
    installVisibleGitDiffPanel(api, item);
    const detailA = deferredFetch();
    const detailB = deferredFetch();
    api.setFetchForTest(url => String(url).includes(`commit=${shaA}`) ? detailA.promise : detailB.promise);
    const loadA = api.setGitDiffCommitExpandedForTest(item, shaA, true);
    const loadB = api.setGitDiffCommitExpandedForTest(item, shaB, true);
    detailB.resolve(jsonResponse({repo: '/repo', sha: shaB, parents: [shaA], from_ref: shaA, to_ref: shaB, message: 'second\n\nbody', message_truncated: false, files: [{status: 'M', path: 'b.js', old_path: '', added: 1, removed: 0, binary: false, counts_available: true}], files_truncated: false, truncated: false}));
    detailA.resolve(jsonResponse({
      repo: '/repo', sha: shaA, parents: [], from_ref: '0'.repeat(40), to_ref: shaA,
      message: '<script>first</script>\n\nbody', message_truncated: true,
      files: [
        {status: 'R', path: 'src/new.js', old_path: 'old.js', added: 5, removed: 2, binary: false, counts_available: true},
        {status: 'M', path: 'assets/data.bin', old_path: '', added: null, removed: null, binary: true, counts_available: true},
        {status: 'D', path: 'gone.txt', old_path: '', added: 0, removed: 4, binary: false, counts_available: true},
      ], files_truncated: true, truncated: true,
    }));
    await Promise.all([loadA, loadB]);
    const state = api.gitDiffTabStateForTest(item);
    assert.deepStrictEqual([...state.expanded].sort(), [shaA, shaB], 'multiple commits remain expanded');
    assert.equal([...state.commitRecords.values()].filter(record => record.detail).length, 2, 'one detail request does not invalidate another SHA');
    const model = api.gitDiffCommitFileTreeForTest(state.commitRecords.get(shaA).detail);
    assert.equal(model.sessionFilesMap.get('/repo/src/new.js').status, 'R');
    assert.equal(model.sessionFilesMap.get('/repo/src/new.js').old_path, 'old.js');
    assert.equal(model.sessionFilesMap.get('/repo/assets/data.bin').binary, true);
    assert.equal(model.sessionFilesMap.get('/repo/gone.txt').status, 'D');
    const historicalItem = api.gitDiffHistoricalFileItemForTest(state.commitRecords.get(shaA).detail, model.sessionFilesMap.get('/repo/src/new.js'));
    assert.deepStrictEqual(canonical(api.historicalFileEditorIdentity(historicalItem)), {path: '/repo/src/new.js', fromRef: '0'.repeat(40), toRef: shaA});
    assert.equal(api.gitDiffCommitMessageForTest(state.commitRecords.get(shaA).detail).textContent, '<script>first</script>\n\nbody', 'commit messages are rendered as text');
  });

}

module.exports = {runGitDiffHistorySuite};

if (require.main === module) runSuites([runGitDiffHistorySuite]);
