// Regression for the 0.7.3 P0 "open file flops to (missing on disk)" defect. A stale or racing
// directory listing or exact-path /api/fs/info result must NEVER mark an already-open text editor
// missing by itself: only an authoritative exact-path /api/fs/read 404 (genuine deletion) may, and that 404 is
// fenced against a newer per-path render so a stale/late 404 cannot overwrite valid content.
//
// Both reload lanes are covered SEPARATELY, each with the same three cases (red-before/green-after):
//   1. a directory-listing omission alone does NOT mark the open editor missing;
//   2. a stale/late 404 cannot overwrite a newer valid read for the same path;
//   3. a genuine exact-path 404 / deletion STILL marks the editor missing.
// Lane A: reloadOpenFileFromDisk (explicit/background reload -> loadOpenFileStateFromDisk).
// Lane B: refreshOpenFilesIfChanged (~1/sec visible-tab poll -> refreshOpenFileFromFetchedStatus).
const {
  assert,
  deferredFetch,
  loadYomux,
  jsonResponse,
  flushAsyncWork,
  testAsync,
  runSuites,
} = require('./browser_helpers/layout_test_helper');

const MD = '/repo/app/notes/big.md';
const OTHER_MD = '/repo/app/notes/other.md';
const CONTENT = '# Title\n\nbody '.padEnd(18943, 'x') + '\n';
const ENTRY = {name: 'big.md', realpath: MD, file_id: 'dev:1:ino:42'};

function readOkResponse(mtime = 5) {
  return jsonResponse({
    path: MD, content: CONTENT, size: 18943, mtime, mtime_ns: mtime,
    realpath: MD, file_id: 'dev:1:ino:42', git_root: '/repo/app', git_tracked: true,
    git_history: [], git_has_history: false,
  });
}

function infoOkResponse(mtime = 5) {
  return jsonResponse({path: MD, name: 'big.md', kind: 'file', size: 18943, mtime, mtime_ns: mtime, realpath: MD});
}

function batchListResponse(reqBody, {includeFile, name = 'big.md', size = 18943, mtime = 5}) {
  const requests = JSON.parse(reqBody || '{}').requests || [];
  return jsonResponse({
    responses: requests.map(r => ({
      id: r.id, ok: true, status: 200,
      payload: {
        path: r.path,
        entries: includeFile
          ? [{name, size, mtime, mtime_ns: mtime, is_dir: false}]
          : [],
      },
    })),
  });
}

// Per-item batch response marking every requested exact-path info as a 404 (used to drive the
// command-palette exact-path validator, which fetches /api/fs/info through the shared fs batch).
function batchInfo404Response(reqBody) {
  const requests = JSON.parse(reqBody || '{}').requests || [];
  return jsonResponse({responses: requests.map(r => ({id: r.id, ok: false, status: 404}))});
}

const IMG = '/repo/app/pic.png';
function imageInfoOkResponse(mtime = 5) {
  return jsonResponse({path: IMG, kind: 'file', size: 1024, mtime, mtime_ns: mtime, is_dir: false, realpath: IMG});
}

// jsDebug API-FAILURE residue whose request URL/endpoint contains `needle`. The strict browser error
// gate flags any such release-blocking failure event at teardown; a controlled deletion-confirmation
// probe's expected 404 must leave NONE.
function apiFailureResidueFor(api, needle) {
  return (api.jsDebugFailureEventsForTest('all') || [])
    .filter(event => String(event.url || event.endpoint || '').includes(needle))
    .map(event => ({type: event.type, status: event.status, url: event.url || event.endpoint}));
}

async function flush(n = 8) { for (let i = 0; i < n; i++) await flushAsyncWork(); }

function stateOf(api) {
  const s = api.currentFileStateForTest(MD);
  if (!s) return {present: false};
  return {present: true, kind: s.kind, externalMissing: s.externalMissing === true, contentLen: (s.content || '').length};
}

function currentStateOf(api, path) {
  const s = api.currentFileStateForTest(path);
  return s ? {...s} : null;
}

// Open MD to a valid loaded editor. Passing an entry means the open reads the exact path directly
// (no directory listing), matching the real "already open, rendering content" starting point.
async function openValid(api) {
  api.setFetchForTest((url) => {
    if (String(url).startsWith('/api/fs/read')) return Promise.resolve(readOkResponse());
    return Promise.resolve(jsonResponse({ok: true}));
  });
  await api.openFileInEditorForTest(MD, ENTRY, {viewMode: 'edit'});
  await flush();
  const st = stateOf(api);
  assert.equal(st.present && st.kind === 'text' && !st.externalMissing, true, 'precondition: MD opens to valid rendered content');
}

// Drive one explicit reload whose directory listing OMITS MD, with a chosen /api/fs/read handler.
async function reloadWithOmittedListing(api, readHandler, infoHandler = () => Promise.resolve(infoOkResponse(6))) {
  api.setFetchForTest((url, options = {}) => {
    const text = String(url);
    if (text.startsWith('/api/fs/read')) return readHandler(text, options);
    if (text.startsWith('/api/fs/info')) return infoHandler(text, options);
    if (text.startsWith('/api/fs/batch')) return Promise.resolve(batchListResponse(options.body, {includeFile: false}));
    return Promise.resolve(jsonResponse({ok: true}));
  });
  const p = api.reloadOpenFileFromDiskForTest(MD);
  await flush(2);
  api.flushFileExplorerFsBatchForTest();
  await flush(4);
  return p;
}

// Apply a genuinely-newer valid render for MD concurrently with a still-in-flight missing verdict.
// A direct entry-bearing loadOpenFileStateFromDisk is the real disk-apply funnel that both lanes reach;
// called directly it runs its own exact read without joining the held reload flight, so it lands newer
// content and advances the per-path content generation. A stale 404 completing afterward must lose.
async function applyNewerValidRender(api, mtime) {
  api.setFetchForTest((url) => {
    if (String(url).startsWith('/api/fs/read')) return Promise.resolve(readOkResponse(mtime));
    return Promise.resolve(jsonResponse({ok: true}));
  });
  await api.loadOpenFileStateFromDiskForTest(MD, {name: 'big.md', size: 18943, mtime, mtime_ns: mtime, is_dir: false});
  await flush(4);
}

async function runOpenFileMissingGuardSuite() {
  await testAsync('layout registration preserves an ordinary read already in flight', async () => {
    const api = loadYomux('', ['1']);
    const path = '/repo/chart.mmd';
    const read = deferredFetch();
    api.setFetchForTest(url => String(url).startsWith('/api/fs/read')
      ? read.promise : Promise.resolve(jsonResponse({ok: true})));
    const opening = api.openFileInEditorForTest(path, {name: 'chart.mmd', mtime: 20, size: 30}, {viewMode: 'edit'});
    await flush(2);
    const loading = api.currentFileStateForTest(path);
    assert.equal(loading.loading, true);
    assert.equal(loading.mtime, 20);
    api.registerFileEditorLayoutItemForTest(path);
    assert.equal(api.currentFileStateForTest(path).loadingPromise, loading.loadingPromise);
    assert.equal(loading.mtime, 20);
    read.resolve(jsonResponse({content: 'graph TD; A-->B', mtime: 20}));
    await opening;
    await flush();
    assert.equal(api.currentFileStateForTest(path).kind, 'text');
    assert.equal(api.currentFileStateForTest(path).content, 'graph TD; A-->B');
    assert.notEqual(api.currentFileStateForTest(path).loading, true);
  });

  for (const outcome of ['success', 'error', 'sniff']) {
    await testAsync(`restore completion preserves in-place newer dirty content (${outcome})`, async () => {
      const api = loadYomux('', ['1']);
      const path = '/repo/restore.md';
      const item = api.fileEditorItemForTest(path);
      api.setOpenFileStateForTest(path, {kind: 'file', original: '', content: '', dirty: false});
      api.registerFileEditorLayoutItemForTest(path, {item});
      const read = deferredFetch();
      const sniff = deferredFetch();
      api.setFetchForTest(url => String(url).startsWith('/api/fs/info') ? sniff.promise : read.promise);
      api.loadFileEditorStateForTest(path, null, item);
      if (outcome === 'sniff') {
        read.resolve(jsonResponse({error: 'binary'}, 415));
        await flush();
      }
      api.patchOpenFileStateForTest(path, {kind: 'text', content: 'new local edit', dirty: true});
      if (outcome === 'sniff') sniff.resolve(jsonResponse({kind: 'file', preview_mime: 'application/pdf'}));
      else read.resolve(outcome === 'error' ? jsonResponse({error: 'missing'}, 404) : jsonResponse({content: 'old disk', mtime: 1}));
      await flush();
      const state = api.currentFileStateForTest(path);
      assert.equal(state.content, 'new local edit');
      assert.equal(state.dirty, true);
      assert.equal(state.kind, 'text');
    });
  }

  for (const path of ['/repo/restored.md', '/repo/restored.png']) {
    for (const responseStatus of [200, 404]) {
      await testAsync(`obsolete restored ${path} response ${responseStatus} cannot resurrect or replace a reopened file`, async () => {
        const api = loadYomux('', ['1']);
        const item = api.registerFileEditorLayoutItemForTest(path);
        const delayed = deferredFetch();
        api.setFetchForTest(() => delayed.promise);
        api.loadFileEditorStateForTest(path, null, item);
        await flush(2);
        assert.equal(await api.closeFileTabForTest(path), true);
        api.setOpenFileStateForTest(path, {kind: path.endsWith('.png') ? 'image' : 'text', original: 'new', content: 'new', dirty: false});
        api.registerFileEditorLayoutItemForTest(path);
        delayed.resolve(jsonResponse({kind: 'file', content: 'old', size: 12, mtime: 1}, responseStatus));
        await flush(12);
        assert.equal(api.currentFileStateForTest(path).content, 'new');
        assert.equal(api.currentFileStateForTest(path).externalMissing, undefined);
      });
    }
  }

  await testAsync('reload preserves edits made while a disk response is held', async () => {
    const api = loadYomux('', ['1']);
    await openValid(api);
    const delayed = deferredFetch();
    api.setFetchForTest(() => delayed.promise);
    const reload = api.loadOpenFileStateFromDiskForTest(MD, {name: 'big.md', kind: 'file', size: 100});
    api.patchOpenFileStateForTest(MD, {content: 'new edit', dirty: true});
    delayed.resolve(readOkResponse(6));
    assert.equal(await reload, false);
    assert.equal(api.currentFileStateForTest(MD).content, 'new edit');
    assert.equal(api.currentFileStateForTest(MD).dirty, true);
  });

  await testAsync('joining a reload cannot renew its fence after newer edits', async () => {
    const api = loadYomux('', ['1']);
    await openValid(api);
    const delayed = deferredFetch();
    let reads = 0;
    api.setFetchForTest(url => {
      if (String(url).includes('include_git=1')) return Promise.resolve(jsonResponse({}));
      reads += 1;
      return reads === 1 ? delayed.promise : Promise.resolve(readOkResponse(7));
    });
    const first = api.replaceOpenFileStateFromDiskForTest(MD, {name: 'big.md', kind: 'file', mtime: 6});
    const second = api.replaceOpenFileStateFromDiskForTest(MD, {name: 'big.md', kind: 'file', mtime: 7});
    api.patchOpenFileStateForTest(MD, {content: 'new local edit', dirty: true});
    delayed.resolve(readOkResponse(6));
    assert.equal(await first, false);
    assert.equal(await second, false);
    assert.equal(reads, 1);
    assert.equal(api.currentFileStateForTest(MD).content, 'new local edit');
    assert.equal(api.currentFileStateForTest(MD).dirty, true);
  });

  for (const status of [404, 413, 503]) {
    await testAsync(`typed reload error ${status} preserves entry metadata`, async () => {
      const api = loadYomux('', ['1']);
      await openValid(api);
      api.setFetchForTest(() => Promise.resolve(jsonResponse({error: 'read rejected'}, status)));
      await api.loadOpenFileStateFromDiskForTest(MD, {name: 'big.md', kind: 'file', mtime: 7, size: 42});
      const state = api.currentFileStateForTest(MD);
      assert.equal(state.mtime, 7);
      assert.equal(state.size, 42);
      assert.equal(state.externalMissing === true, status === 404);
    });
  }

  for (const omittedListing of [false, true]) {
  await testAsync(`reload retains known Git metadata until enrichment completes (omitted listing: ${omittedListing})`, async () => {
    const api = loadYomux('', ['1']);
    await openValid(api);
    api.patchOpenFileStateForTest(MD, {gitRoot: '/repo/app', gitTracked: true, gitHasHistory: true, gitHistory: [{ref: 'HEAD'}, {ref: 'HEAD~1'}]});
    const metadata = deferredFetch();
    api.setFetchForTest(url => String(url).includes('include_git=1') ? metadata.promise : String(url).startsWith('/api/fs/info') ? Promise.resolve(jsonResponse({}, 404)) : Promise.resolve(jsonResponse({content: 'new disk', mtime: 6, git_enrichment: {reason: 'deferred'}}))); api.markFileExplorerDeletedPathsForTest([MD]);
    assert.equal(await api.loadOpenFileStateFromDiskForTest(MD, omittedListing ? null : {name: 'big.md', kind: 'file', size: 100}), true);
    assert.equal(api.currentFileStateForTest(MD).gitTracked, true);
    assert.equal(api.currentFileStateForTest(MD).gitHasHistory, true);
    metadata.resolve(jsonResponse({git_root: '/repo/app', git_tracked: true, git_has_history: true, git_history: [{ref: 'HEAD'}, {ref: 'HEAD~1'}]}));
    await flush();
    assert.equal(api.currentFileStateForTest(MD).content, 'new disk');
  });
  }

  await testAsync('restored direct text read schedules metadata-only Git enrichment', async () => {
    const api = loadYomux('', ['1']);
    const path = '/repo/restore.md';
    const item = api.fileEditorItemForTest(path);
    api.setOpenFileStateForTest(path, {kind: 'file', content: '', original: '', dirty: false});
    api.registerFileEditorLayoutItemForTest(path, {item});
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      return Promise.resolve(jsonResponse(String(url).includes('include_git=1')
        ? {git_root: '/repo', git_tracked: true, git_has_history: true, git_history: [{ref: 'HEAD'}]}
        : {content: 'restored', mtime: 1, git_enrichment: {reason: 'deferred'}}));
    });
    api.loadFileEditorStateForTest(path, null, item);
    await flush();
    assert.equal(api.currentFileStateForTest(path).content, 'restored');
    assert.equal(api.currentFileStateForTest(path).gitTracked, true);
    assert.equal(requests.filter(url => url.includes('/api/fs/info?') && url.includes('include_git=1')).length, 1);
  });

  await testAsync('poll lane: exact-path status checks progress independently', async () => {
    const api = loadYomux('', ['1']);
    for (const path of [MD, OTHER_MD]) {
      api.setOpenFileStateForTest(path, {
        kind: 'text', mtime: 5, size: 100, dirty: true, content: 'local', original: 'disk',
      });
    }
    const held = deferredFetch();
    const requests = [];
    api.setFetchForTest((url) => {
      const text = String(url);
      requests.push(text);
      if (text.startsWith('/api/fs/info?') && text.includes(encodeURIComponent(MD))) return held.promise;
      if (text.startsWith('/api/fs/info?') && text.includes(encodeURIComponent(OTHER_MD))) {
        return Promise.resolve(jsonResponse({path: OTHER_MD, name: 'other.md', kind: 'file', size: 100, mtime: 6, mtime_ns: 6}));
      }
      return Promise.resolve(jsonResponse({ok: true}));
    });

    const poll = api.refreshOpenFilesIfChangedForTest({paths: [MD, OTHER_MD]});
    await flush(5);
    const other = currentStateOf(api, OTHER_MD);
    assert.ok(requests.some(url => url.startsWith('/api/fs/info?') && url.includes(encodeURIComponent(MD))), 'first file uses exact-path info');
    assert.ok(requests.some(url => url.startsWith('/api/fs/info?') && url.includes(encodeURIComponent(OTHER_MD))), 'second file uses exact-path info');
    assert.equal(requests.some(url => url.startsWith('/api/fs/fast/list') || url.startsWith('/api/fs/list')), false, 'status checks do not list a parent directory');
    assert.equal(other?.externalChanged?.mtime, 6, 'the second file status applies while the first response is still held');

    held.resolve(jsonResponse({path: MD, name: 'big.md', kind: 'file', size: 100, mtime: 5, mtime_ns: 5}));
    await poll;
    await flush();
  });

  await testAsync('poll lane: a fresh cached directory row supplies exact file status', async () => {
    const api = loadYomux('', ['1']);
    api.setOpenFileStateForTest(MD, {
      kind: 'text', mtime: 5, size: 100, dirty: true, content: 'local', original: 'disk',
    });
    api.setFetchForTest(url => {
      assert.ok(String(url).startsWith('/api/fs/fast/list?'), 'the cache is seeded by one direct directory read');
      return Promise.resolve(jsonResponse({entries: [{name: 'big.md', kind: 'file', size: 100, mtime: 5, mtime_ns: 5}]}));
    });
    await api.fetchDirectoryForTest('/repo/app/notes', {user: true, enrich: false});

    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      return Promise.resolve(jsonResponse({ok: true}));
    });
    await api.refreshOpenFilesIfChangedForTest({paths: [MD]});
    assert.deepEqual(requests, [], 'a fresh positive listing row avoids another filesystem request');
  });

  await testAsync('poll lane: an exact status that becomes a directory keeps loaded file content', async () => {
    const api = loadYomux('', ['1']);
    api.setOpenFileStateForTest(MD, {
      kind: 'text', mtime: 5, size: 100, dirty: false, content: 'loaded text', original: 'loaded text',
    });
    api.setFetchForTest(url => {
      assert.ok(String(url).startsWith('/api/fs/info?'), 'the poll verifies the exact changed path');
      return Promise.resolve(jsonResponse({path: MD, name: 'big.md', kind: 'dir', size: null}));
    });

    await api.refreshOpenFilesIfChangedForTest({paths: [MD]});
    const state = currentStateOf(api, MD);
    assert.equal(state.kind, 'text', 'a directory replacement does not replace loaded editor content with a read error');
    assert.equal(state.content, 'loaded text', 'the last valid text remains available');
    assert.equal(state.externalMissing, undefined, 'a directory is not reported as a deleted path');
    assert.ok(state.externalError, 'the editor reports that the path can no longer be inspected as a file');
  });

  await testAsync('poll lane: exact status clears a deletion tombstone and seeds the next cache read', async () => {
    const api = loadYomux('', ['1']);
    api.setOpenFileStateForTest(MD, {
      kind: 'text', mtime: 5, size: 100, dirty: true, content: 'local', original: 'disk',
    });
    api.markFileExplorerDeletedPathsForTest([MD]);
    let requests = 0;
    api.setFetchForTest(url => {
      requests += 1;
      assert.ok(String(url).startsWith('/api/fs/info?'), 'tombstoned status uses authoritative exact-path info');
      return Promise.resolve(infoOkResponse(5));
    });

    await api.refreshOpenFilesIfChangedForTest({paths: [MD]});
    assert.deepEqual(api.fileExplorerDeletedPathTombstonesForTest(), [], 'a successful exact-path stat clears the tombstone');
    assert.equal(requests, 1, 'the first poll performs one exact-path request');
    await api.refreshOpenFilesIfChangedForTest({paths: [MD]});
    assert.equal(requests, 1, 'the next poll reuses the bounded positive status cache');
  });

  await testAsync('poll lane: a newer directory listing supersedes an older cached exact status', async () => {
    const api = loadYomux('', ['1']);
    api.setOpenFileStateForTest(MD, {
      kind: 'text', mtime: 5, size: 100, dirty: true, content: 'local', original: 'disk',
    });
    let infoRequests = 0;
    api.setFetchForTest(url => {
      assert.ok(String(url).startsWith('/api/fs/info?'), 'only the initial uncached status reaches the server');
      infoRequests += 1;
      return Promise.resolve(infoOkResponse(5));
    });

    await api.refreshOpenFilesIfChangedForTest({paths: [MD]});
    api.setFileExplorerFsResourceValueForTest('/repo/app/notes', [
      {name: 'big.md', kind: 'file', size: 100, mtime: 6, mtime_ns: 6, realpath: MD},
    ]);
    await api.refreshOpenFilesIfChangedForTest({paths: [MD]});

    const state = currentStateOf(api, MD);
    assert.equal(infoRequests, 1, 'the newer listing row supplies status without another request');
    assert.equal(state.externalChanged?.mtime, 6, 'the newer directory metadata wins over the older status cache');
  });

  await testAsync('poll lane: a listing published during exact status lookup fences the older response', async () => {
    const api = loadYomux('', ['1']);
    api.setOpenFileStateForTest(MD, {
      kind: 'text', mtime: 5, size: 100, dirty: true, content: 'local', original: 'disk',
    });
    const info = deferredFetch();
    api.setFetchForTest(url => {
      assert.ok(String(url).startsWith('/api/fs/info?'), 'the poll has one exact-path request in flight');
      return info.promise;
    });

    const poll = api.refreshOpenFilesIfChangedForTest({paths: [MD]});
    await flush(3);
    api.setFileExplorerFsResourceValueForTest('/repo/app/notes', [
      {name: 'big.md', kind: 'file', size: 100, mtime: 6, mtime_ns: 6, realpath: MD},
    ]);
    info.resolve(jsonResponse({path: MD, name: 'big.md', kind: 'file', size: 100, mtime: 5, mtime_ns: 5}));
    await poll;

    assert.equal(currentStateOf(api, MD).externalChanged?.mtime, 6, 'the older response cannot overwrite newer filesystem evidence');
  });

  await testAsync('poll lane: a status response cannot clear a newer deletion tombstone', async () => {
    const api = loadYomux('', ['1']);
    api.setOpenFileStateForTest(MD, {
      kind: 'text', mtime: 5, size: 100, dirty: true, content: 'local', original: 'disk',
    });
    const info = deferredFetch();
    api.setFetchForTest(url => {
      assert.ok(String(url).startsWith('/api/fs/info?'), 'the poll has one exact-path request in flight');
      return info.promise;
    });

    const poll = api.refreshOpenFilesIfChangedForTest({paths: [MD]});
    await flush(3);
    api.markFileExplorerDeletedPathsForTest([MD]);
    info.resolve(jsonResponse({path: MD, name: 'big.md', kind: 'file', size: 100, mtime: 5, mtime_ns: 5}));
    await poll;

    assert.ok(api.fileExplorerDeletedPathTombstonesForTest().includes(MD), 'an older response cannot erase a newer deletion event');
  });

  await testAsync('poll lane: overlapping status checks for one path share one in-flight request', async () => {
    const api = loadYomux('', ['1']);
    api.setOpenFileStateForTest(MD, {
      kind: 'text', mtime: 5, size: 100, dirty: true, content: 'local', original: 'disk',
    });
    const info = deferredFetch();
    let requests = 0;
    api.setFetchForTest(url => {
      assert.ok(String(url).startsWith('/api/fs/info?'), 'the overlapping polls share exact-path status');
      requests += 1;
      return info.promise;
    });

    const first = api.refreshOpenFilesIfChangedForTest({paths: [MD]});
    const second = api.refreshOpenFilesIfChangedForTest({paths: [MD]});
    await flush(3);
    assert.equal(requests, 1, 'only one lookup owns the path while status is in flight');
    info.resolve(jsonResponse({path: MD, name: 'big.md', kind: 'file', size: 100, mtime: 6, mtime_ns: 6}));
    await Promise.all([first, second]);

    assert.equal(currentStateOf(api, MD).externalChanged?.mtime, 6, 'the shared result reaches the open editor');
  });

  // ---------- Lane A: explicit/background reload (loadOpenFileStateFromDisk) ----------

  await testAsync('reload lane: an exact-path status result that says present keeps the editor loaded', async () => {
    const api = loadYomux('', ['1']);
    await openValid(api);
    await reloadWithOmittedListing(api, () => Promise.resolve(readOkResponse(6)));
    await flush();
    const st = stateOf(api);
    assert.equal(st.externalMissing, false, 'present exact-path status must not flip the editor to (missing on disk)');
    assert.equal(st.kind, 'text', 'editor still renders text content after a present status result');
    assert.ok(st.contentLen > 0, 'loaded content is preserved');
  });

  // A late/stale exact-path status response must not overwrite a newer valid render.
  await testAsync('reload lane: a late/stale status response cannot overwrite a newer valid read', async () => {
    const api = loadYomux('', ['1']);
    await openValid(api);
    await applyNewerValidRender(api, 6); // a newer valid render lands first (content mtime 6)
    const infoD = deferredFetch();
    api.setFetchForTest((url, options = {}) => {
      const text = String(url);
      if (text.startsWith('/api/fs/read')) return Promise.resolve(readOkResponse(6)); // exact path present
      if (text.startsWith('/api/fs/info')) return infoD.promise;
      return Promise.resolve(jsonResponse({ok: true}));
    });
    const reloadP = api.reloadOpenFileFromDiskForTest(MD);
    await flush(2);
    infoD.resolve(infoOkResponse(6));
    await reloadP.catch(() => {});
    await flush();
    const st = stateOf(api);
    assert.equal(st.externalMissing, false, 'a late status result must not overwrite the newer valid read');
    assert.equal(st.kind, 'text', 'the newer valid content survives a stale status result');
  });

  await testAsync('reload lane: a genuine exact-path 404 STILL marks the editor missing', async () => {
    const api = loadYomux('', ['1']);
    await openValid(api);
    await reloadWithOmittedListing(api, () => Promise.resolve(jsonResponse({}, 404)), () => Promise.resolve(jsonResponse({}, 404)));
    await flush();
    const st = stateOf(api);
    assert.equal(st.externalMissing, true, 'a real exact-path 404 (deletion) marks the editor missing');
    assert.equal(st.kind, 'error', 'genuine-missing state is preserved');
    // The confirmation probe's expected 404 is this lane's deletion verdict, NOT an API failure: it must
    // leave no jsDebug/API-failure residue (else strict browser error gate teardown flags it).
    assert.deepEqual(apiFailureResidueFor(api, '/api/fs/read'), [], 'genuine deletion marks missing without a jsDebug /api/fs/read failure residue');
  });

  // ---------- Lane B: ~1/sec visible-tab poll (refreshOpenFileFromFetchedStatus) ----------

  async function poll(api) {
    const p = api.refreshOpenFilesIfChangedForTest({paths: [MD]});
    await flush(2);
    api.flushFileExplorerFsBatchForTest();
    await flush(4);
    return p;
  }

  await testAsync('poll lane: an exact-path status result that says present keeps the editor loaded', async () => {
    const api = loadYomux('', ['1']);
    await openValid(api);
    api.setFetchForTest((url, options = {}) => {
      const text = String(url);
      if (text.startsWith('/api/fs/read')) return Promise.resolve(readOkResponse(6));
      if (text.startsWith('/api/fs/info')) return Promise.resolve(infoOkResponse(6));
      if (text.startsWith('/api/fs/batch')) return Promise.resolve(batchListResponse(options.body, {includeFile: false}));
      return Promise.resolve(jsonResponse({ok: true}));
    });
    await poll(api);
    await flush();
    const st = stateOf(api);
    assert.equal(st.externalMissing, false, 'poll must not flip an open editor missing on a present exact-path status');
    assert.equal(st.kind, 'text', 'poll preserves rendered content when the exact path still exists');
  });

  await testAsync('poll lane: a late/stale status response cannot overwrite a newer valid read', async () => {
    const api = loadYomux('', ['1']);
    await openValid(api);
    await applyNewerValidRender(api, 6);
    const infoD = deferredFetch();
    api.setFetchForTest((url, options = {}) => {
      const text = String(url);
      if (text.startsWith('/api/fs/read')) return Promise.resolve(readOkResponse(6));
      if (text.startsWith('/api/fs/info')) return infoD.promise;
      return Promise.resolve(jsonResponse({ok: true}));
    });
    const pollP = api.refreshOpenFilesIfChangedForTest({paths: [MD]});
    await flush(2);
    infoD.resolve(infoOkResponse(6));
    await pollP.catch(() => {});
    await flush();
    const st = stateOf(api);
    assert.equal(st.externalMissing, false, 'poll-lane late status must not overwrite the newer valid read');
    assert.equal(st.kind, 'text', 'the newer valid content survives a stale status response');
  });

  await testAsync('poll lane: a genuine exact-path 404 STILL marks the editor missing', async () => {
    const api = loadYomux('', ['1']);
    await openValid(api);
    api.setFetchForTest((url, options = {}) => {
      const text = String(url);
      if (text.startsWith('/api/fs/read')) return Promise.resolve(jsonResponse({}, 404));
      if (text.startsWith('/api/fs/info')) return Promise.resolve(jsonResponse({}, 404));
      if (text.startsWith('/api/fs/batch')) return Promise.resolve(batchListResponse(options.body, {includeFile: false}));
      return Promise.resolve(jsonResponse({ok: true}));
    });
    await poll(api);
    await flush();
    const st = stateOf(api);
    assert.equal(st.externalMissing, true, 'poll marks missing on a genuine exact-path 404 / deletion');
    assert.equal(st.kind, 'error', 'genuine-missing state is preserved on the poll lane');
  });

  // ---------- Media lane: loadFileEditorState (image/media) ----------
  // Media editors are proven present by the authoritative /api/fs/info stat, not a byte read. A
  // directory-listing omission must never flip an open media view to "missing".

  await testAsync('media lane: a directory-listing omission alone does NOT mark an open media editor missing', async () => {
    const api = loadYomux('', ['1']);
    const item = api.fileEditorItemForTest(IMG);
    api.setOpenFileStateForTest(IMG, {kind: 'image', size: 1024, mtime: 5, mtime_ns: 5, original: '', content: '', dirty: false});
    api.setFetchForTest((url, options = {}) => {
      const text = String(url);
      if (text.startsWith('/api/fs/info')) return Promise.resolve(imageInfoOkResponse(5)); // exact path present
      if (text.startsWith('/api/fs/batch')) return Promise.resolve(batchListResponse(options.body, {includeFile: false})); // omit
      return Promise.resolve(jsonResponse({ok: true}));
    });
    api.loadFileEditorStateForTest(IMG, null, item);
    await flush(2);
    api.flushFileExplorerFsBatchForTest();
    await flush(6);
    const s = api.currentFileStateForTest(IMG);
    assert.equal(s.externalMissing === true, false, 'media omission alone must not flip the editor to missing');
    assert.equal(s.kind, 'image', 'authoritative /api/fs/info confirms present; the image view survives the omission');
  });

  await testAsync('media lane: a late/stale omitting listing cannot overwrite a newer valid media render', async () => {
    const api = loadYomux('', ['1']);
    const item = api.fileEditorItemForTest(IMG);
    api.setOpenFileStateForTest(IMG, {kind: 'image', size: 1024, mtime: 5, mtime_ns: 5, original: '', content: '', dirty: false});
    // A newer valid media render lands first (good listing includes pic.png; advances the generation).
    api.setFetchForTest((url, options = {}) => {
      const text = String(url);
      if (text.startsWith('/api/fs/info')) return Promise.resolve(imageInfoOkResponse(6));
      if (text.startsWith('/api/fs/batch')) return Promise.resolve(batchListResponse(options.body, {includeFile: true, name: 'pic.png', size: 1024, mtime: 6}));
      return Promise.resolve(jsonResponse({ok: true}));
    });
    api.loadFileEditorStateForTest(IMG, null, item);
    await flush(2);
    api.flushFileExplorerFsBatchForTest();
    await flush(6);
    // Then a stale reload whose directory listing OMITS pic.png, arriving late.
    const listD = deferredFetch();
    let lastBatchBody = null;
    api.setFetchForTest((url, options = {}) => {
      const text = String(url);
      if (text.startsWith('/api/fs/info')) return Promise.resolve(imageInfoOkResponse(6));
      if (text.startsWith('/api/fs/batch')) { lastBatchBody = options.body; return listD.promise; }
      return Promise.resolve(jsonResponse({ok: true}));
    });
    api.loadFileEditorStateForTest(IMG, null, item);
    await flush(2);
    api.flushFileExplorerFsBatchForTest();
    await flush(2);
    listD.resolve(batchListResponse(lastBatchBody, {includeFile: false}));
    await flush(6);
    const s = api.currentFileStateForTest(IMG);
    assert.equal(s.externalMissing === true, false, 'a late omitting listing must not overwrite the newer valid media render');
    assert.equal(s.kind, 'image', 'the newer valid image survives the stale listing');
  });

  await testAsync('media lane: a genuine exact-path /api/fs/info 404 STILL marks the media editor missing', async () => {
    const api = loadYomux('', ['1']);
    const item = api.fileEditorItemForTest(IMG);
    api.setOpenFileStateForTest(IMG, {kind: 'image', size: 1024, mtime: 5, mtime_ns: 5, original: '', content: '', dirty: false});
    api.setFetchForTest((url, options = {}) => {
      const text = String(url);
      if (text.startsWith('/api/fs/info')) return Promise.resolve(jsonResponse({}, 404)); // genuine deletion
      if (text.startsWith('/api/fs/batch')) return Promise.resolve(batchListResponse(options.body, {includeFile: false}));
      return Promise.resolve(jsonResponse({ok: true}));
    });
    api.loadFileEditorStateForTest(IMG, null, item);
    await flush(2);
    api.flushFileExplorerFsBatchForTest();
    await flush(6);
    const s = api.currentFileStateForTest(IMG);
    assert.equal(s.externalMissing === true, true, 'a genuine exact-path /api/fs/info 404 marks the media editor missing');
    // The media confirmation probe's expected /api/fs/info 404 is the deletion verdict, not a failure.
    assert.deepEqual(apiFailureResidueFor(api, '/api/fs/info'), [], 'genuine media deletion marks missing without a jsDebug /api/fs/info failure residue');
  });

  // ---------- Command-palette tab validator (20_layout_state.js) ----------
  // The validator uses an authoritative exact-path /api/fs/info check, but its "missing" outcome must be
  // fenced against newer per-path state so a stale/late 404 cannot overwrite a newer valid render.

  await testAsync('command-palette validation: a stale/late 404 cannot overwrite a newer valid render', async () => {
    const api = loadYomux('', ['1']);
    await openValid(api);
    const item = api.fileEditorItemForTest(MD);
    const infoD = deferredFetch();
    let lastBatchBody = null;
    api.setFetchForTest((url, options = {}) => {
      const text = String(url);
      if (text.startsWith('/api/fs/read')) return Promise.resolve(readOkResponse(5));
      if (text.startsWith('/api/fs/batch')) { lastBatchBody = options.body; return infoD.promise; } // exact-path info held
      return Promise.resolve(jsonResponse({ok: true}));
    });
    api.commandPaletteValidateFileTabPathsForTest([item]); // captures generation, queues exact-path info
    await flush(2);
    api.flushFileExplorerFsBatchForTest(); // dispatch; the info 404 is held on infoD
    await flush(2);
    await applyNewerValidRender(api, 7); // a newer valid render advances the generation
    infoD.resolve(batchInfo404Response(lastBatchBody)); // the stale exact-path 404 completes afterward
    await flush(6);
    const st = stateOf(api);
    assert.equal(st.externalMissing, false, 'a stale command-palette 404 must not overwrite the newer valid render');
    assert.equal(st.kind, 'text', 'the newer valid content wins the command-palette generation race');
  });

  await testAsync('command-palette validation: a genuine exact-path 404 STILL marks the tab missing', async () => {
    const api = loadYomux('', ['1']);
    await openValid(api);
    const item = api.fileEditorItemForTest(MD);
    api.setFetchForTest((url, options = {}) => {
      const text = String(url);
      if (text.startsWith('/api/fs/batch')) return Promise.resolve(batchInfo404Response(options.body));
      return Promise.resolve(jsonResponse({ok: true}));
    });
    api.commandPaletteValidateFileTabPathsForTest([item]);
    await flush(2);
    api.flushFileExplorerFsBatchForTest();
    await flush(6);
    const st = stateOf(api);
    assert.equal(st.externalMissing, true, 'a genuine exact-path 404 marks the tab missing');
  });
}

module.exports = {runOpenFileMissingGuardSuite};

if (require.main === module) {
  runSuites([runOpenFileMissingGuardSuite]);
}
