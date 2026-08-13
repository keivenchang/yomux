// Regression for the 0.7.3 P0 "open file flops to (missing on disk)" defect. A stale or racing
// directory listing (/api/fs/batch) that OMITS an already-open file must NEVER mark that editor
// missing: only an authoritative exact-path /api/fs/read 404 (genuine deletion) may, and that 404 is
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
  loadYolomux,
  jsonResponse,
  flushAsyncWork,
  testAsync,
  runSuites,
} = require('./browser_helpers/layout_test_helper');

const MD = '/repo/app/notes/big.md';
const CONTENT = '# Title\n\nbody '.padEnd(18943, 'x') + '\n';
const ENTRY = {name: 'big.md', realpath: MD, file_id: 'dev:1:ino:42'};

function readOkResponse(mtime = 5) {
  return jsonResponse({
    path: MD, content: CONTENT, size: 18943, mtime, mtime_ns: mtime,
    realpath: MD, file_id: 'dev:1:ino:42', git_root: '/repo/app', git_tracked: true,
    git_history: [], git_has_history: false,
  });
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
async function reloadWithOmittedListing(api, readHandler) {
  api.setFetchForTest((url, options = {}) => {
    const text = String(url);
    if (text.startsWith('/api/fs/read')) return readHandler(text, options);
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
  // ---------- Lane A: explicit/background reload (loadOpenFileStateFromDisk) ----------

  await testAsync('reload lane: a directory-listing omission alone does NOT mark an open editor missing', async () => {
    const api = loadYolomux('', ['1']);
    await openValid(api);
    // Listing omits MD, but the exact-path read still returns its bytes -> file is present.
    await reloadWithOmittedListing(api, () => Promise.resolve(readOkResponse(6)));
    await flush();
    const st = stateOf(api);
    assert.equal(st.externalMissing, false, 'omission alone must not flip the editor to (missing on disk)');
    assert.equal(st.kind, 'text', 'editor still renders text content after a stale omitting listing');
    assert.ok(st.contentLen > 0, 'loaded content is preserved');
  });

  // A late/stale directory listing that OMITS the path, arriving after a newer valid render, must not
  // overwrite it. The unfixed owner marks missing straight off the omission (red); the fix confirms the
  // exact path first, sees its bytes, and keeps the newer render (green).
  await testAsync('reload lane: a late/stale omitting listing cannot overwrite a newer valid read', async () => {
    const api = loadYolomux('', ['1']);
    await openValid(api);
    await applyNewerValidRender(api, 6); // a newer valid render lands first (content mtime 6)
    const listD = deferredFetch();
    let lastBatchBody = null;
    api.setFetchForTest((url, options = {}) => {
      const text = String(url);
      if (text.startsWith('/api/fs/read')) return Promise.resolve(readOkResponse(6)); // exact path present
      if (text.startsWith('/api/fs/batch')) { lastBatchBody = options.body; return listD.promise; } // listing held
      return Promise.resolve(jsonResponse({ok: true}));
    });
    const reloadP = api.reloadOpenFileFromDiskForTest(MD);
    await flush(2);
    api.flushFileExplorerFsBatchForTest();
    await flush(2);
    listD.resolve(batchListResponse(lastBatchBody, {includeFile: false})); // LATE omitting listing
    await reloadP.catch(() => {});
    await flush();
    const st = stateOf(api);
    assert.equal(st.externalMissing, false, 'a late omitting listing must not overwrite the newer valid read');
    assert.equal(st.kind, 'text', 'the newer valid content survives a stale listing');
  });

  await testAsync('reload lane: a genuine exact-path 404 STILL marks the editor missing', async () => {
    const api = loadYolomux('', ['1']);
    await openValid(api);
    await reloadWithOmittedListing(api, () => Promise.resolve(jsonResponse({}, 404)));
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

  await testAsync('poll lane: a directory-listing omission alone does NOT mark an open editor missing', async () => {
    const api = loadYolomux('', ['1']);
    await openValid(api);
    api.setFetchForTest((url, options = {}) => {
      const text = String(url);
      if (text.startsWith('/api/fs/read')) return Promise.resolve(readOkResponse(6));
      if (text.startsWith('/api/fs/batch')) return Promise.resolve(batchListResponse(options.body, {includeFile: false}));
      return Promise.resolve(jsonResponse({ok: true}));
    });
    await poll(api);
    await flush();
    const st = stateOf(api);
    assert.equal(st.externalMissing, false, 'poll must not flip an open editor missing on a listing omission');
    assert.equal(st.kind, 'text', 'poll preserves rendered content when the exact path still reads');
  });

  await testAsync('poll lane: a late/stale omitting listing cannot overwrite a newer valid read', async () => {
    const api = loadYolomux('', ['1']);
    await openValid(api);
    await applyNewerValidRender(api, 6);
    const listD = deferredFetch();
    let lastBatchBody = null;
    api.setFetchForTest((url, options = {}) => {
      const text = String(url);
      if (text.startsWith('/api/fs/read')) return Promise.resolve(readOkResponse(6));
      if (text.startsWith('/api/fs/batch')) { lastBatchBody = options.body; return listD.promise; }
      return Promise.resolve(jsonResponse({ok: true}));
    });
    const pollP = api.refreshOpenFilesIfChangedForTest({paths: [MD]});
    await flush(2);
    api.flushFileExplorerFsBatchForTest();
    await flush(2);
    listD.resolve(batchListResponse(lastBatchBody, {includeFile: false}));
    await pollP.catch(() => {});
    await flush();
    const st = stateOf(api);
    assert.equal(st.externalMissing, false, 'poll-lane late omitting listing must not overwrite the newer valid read');
    assert.equal(st.kind, 'text', 'the newer valid content survives a stale poll listing');
  });

  await testAsync('poll lane: a genuine exact-path 404 STILL marks the editor missing', async () => {
    const api = loadYolomux('', ['1']);
    await openValid(api);
    api.setFetchForTest((url, options = {}) => {
      const text = String(url);
      if (text.startsWith('/api/fs/read')) return Promise.resolve(jsonResponse({}, 404));
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
    const api = loadYolomux('', ['1']);
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
    const api = loadYolomux('', ['1']);
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
    const api = loadYolomux('', ['1']);
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
    const api = loadYolomux('', ['1']);
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
    const api = loadYolomux('', ['1']);
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
