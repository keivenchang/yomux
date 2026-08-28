// HTTP 413 means "some limit was exceeded", not "this file is too big". The Git view raises 413 for
// its own object-store and deadline budgets, so opening a 21 KB file in a repository whose objects
// are not packed was reported to the user as "File is too large to preview". The same screen showed
// the literal text "{size}; limit is {limit}", because the too-large state built its detail message
// with no parameters for a template that names two.
//
// Both are covered here, red-before/green-after:
//   1. a 413 carrying a Git budget reason must NOT produce the too-large state;
//   2. a 413 carrying the file-size reason still must, and its detail must carry real numbers.
const {
  assert,
  loadYolomux,
  jsonResponse,
  flushAsyncWork,
  testAsync,
  runSuites,
} = require('./browser_helpers/layout_test_helper');

const MD = '/repo/app/notes/status.md';
const ENTRY = {name: 'status.md', realpath: MD, file_id: 'dev:1:ino:77', size: 21549};

// The real producer is `FilesystemError.file_too_large` in yolomux_lib/filesystem/errors.py, which
// sends `params: {label, size, max}` - not an empty object. A fixture that sends `{}` renders the
// locale template's own placeholder names and still satisfies a state-only assertion, so the
// oversize case below supplies the exact params the server does.
const MAX_READ_BYTES = 20971520;

function refusal(messageKey, fallback, params = {}) {
  return jsonResponse({
    error: fallback,
    user_message: {key: messageKey, params, fallback},
  }, 413);
}

async function flush(n = 8) { for (let i = 0; i < n; i++) await flushAsyncWork(); }

async function openWithRefusal(api, messageKey, fallback, params = {}) {
  api.setFetchForTest((url) => {
    if (String(url).startsWith('/api/fs/read')) return Promise.resolve(refusal(messageKey, fallback, params));
    return Promise.resolve(jsonResponse({ok: true}));
  });
  await api.openFileInEditorForTest(MD, ENTRY, {viewMode: 'edit'});
  await flush();
  return api.currentFileStateForTest(MD) || {};
}

async function runOpenFile413ReasonSuite() {
  await testAsync('a Git budget refusal is not reported as an oversized file', async () => {
    const api = loadYolomux();
    const state = await openWithRefusal(
      api,
      'fs.error.gitHistoryTooLarge',
      'Git repository metadata exceeds the snapshot limit',
    );
    assert.notEqual(state.kind, 'too-large', 'a 21 KB file refused by the Git view is not too large');
  });

  await testAsync('a genuine size refusal still reports an oversized file', async () => {
    const api = loadYolomux();
    const state = await openWithRefusal(api, 'fs.error.tooLarge', 'file too large');
    assert.equal(state.kind, 'too-large', 'the server saying the file is oversized still means oversized');
  });

  await testAsync('an unlabelled 413 is not relabelled as an oversized file', async () => {
    // A 413 with no message key is SOME limit, not a statement about this file's size. Treating it
    // as oversize produced the reported screen: "File is too large to preview" with an empty size
    // before "; limit is 20 MB", because a limit that is not about size has no file size to report.
    // A genuine content oversize always carries `fs.error.tooLarge` from `FilesystemError
    // .file_too_large`, so nothing legitimate depends on this fallback.
    const api = loadYolomux();
    api.setFetchForTest((url) => {
      if (String(url).startsWith('/api/fs/read')) {
        return Promise.resolve(jsonResponse({error: 'some other limit'}, 413));
      }
      return Promise.resolve(jsonResponse({ok: true}));
    });
    await api.openFileInEditorForTest(MD, ENTRY, {viewMode: 'edit'});
    await flush();
    const state = api.currentFileStateForTest(MD) || {};
    assert.notEqual(state.kind, 'too-large', 'an unlabelled limit is not a claim about file size');
    const params = state.error?.user_message?.params || {};
    assert.equal(String(params.size || ''), '', 'no empty size is offered for a non-size limit');
  });

  await testAsync('a labelled oversize renders the real label and numbers, not placeholders', async () => {
    // Fixture parity with the server: `FilesystemError.file_too_large(21549, MAX_READ_BYTES)` emits
    // exactly this descriptor, verified against yolomux_lib/filesystem/errors.py.
    const api = loadYolomux();
    const state = await openWithRefusal(
      api,
      'fs.error.tooLarge',
      `file too large (${ENTRY.size} bytes; max ${MAX_READ_BYTES})`,
      {label: 'file', size: ENTRY.size, max: MAX_READ_BYTES},
    );
    assert.equal(state.kind, 'too-large');
    assert.equal(Number(state.size) > 0, true, 'the state must carry the real file size');
    assert.equal(Number(state.maxBytes) > 0, true, 'the state must carry the preview limit');

    // Assert what the USER reads, through the same renderer the editor uses. A state-only
    // assertion passes even when the screen shows the locale template's placeholder names.
    const rendered = api.fileErrorTextForTest(state.error, 'editor.fileTooLargeDetail', {
      size: String(ENTRY.size),
      limit: String(MAX_READ_BYTES),
    });
    for (const placeholder of ['{label}', '{size}', '{max}', '{limit}']) {
      assert.equal(rendered.includes(placeholder), false, `unresolved ${placeholder} reached the user`);
    }
    assert.equal(rendered.includes('file'), true, 'the rendered detail names the label');
    assert.equal(rendered.includes(String(ENTRY.size)), true, 'the rendered detail names the file size');
    assert.equal(rendered.includes(String(MAX_READ_BYTES)), true, 'the rendered detail names the maximum');
  });

  await testAsync('a readable file opens even when Git enrichment could not be produced', async () => {
    // The required invariant: validation -> content read -> model -> DOM is the file's path, and Git
    // status/history/diff/blame is decoration on top of it. The server already answers this way -
    // `read_file` reports `git_enrichment: {available: false, reason}` instead of failing - so the
    // browser must open that payload as ordinary content, not as an error or an oversize.
    const api = loadYolomux();
    const content = '# STATUS-REPORT\n\nreadable markdown body\n';
    api.setFetchForTest((url) => {
      if (String(url).startsWith('/api/fs/read')) {
        return Promise.resolve(jsonResponse({
          path: MD,
          size: content.length,
          mtime: 1787000000,
          mtime_ns: 1787000000000000000,
          content,
          extension: '.md',
          is_text_extension: true,
          git_root: '',
          git_tracked: false,
          git_history: [],
          git_has_history: false,
          git_enrichment: {available: false, reason: 'fs.error.gitHistoryTooLarge'},
        }, 200));
      }
      return Promise.resolve(jsonResponse({ok: true}));
    });
    await api.openFileInEditorForTest(MD, ENTRY, {viewMode: 'edit'});
    await flush();
    const state = api.currentFileStateForTest(MD) || {};

    assert.equal(state.kind, 'text', 'a degraded Git answer must not change the file kind');
    assert.equal(state.content, content, 'the content that was read must reach the editor model');
    assert.notEqual(state.kind, 'too-large', 'unavailable Git enrichment is not an oversized file');
    assert.equal(state.error == null || state.error === '', true, 'no error state for readable content');
  });
}

module.exports = {runOpenFile413ReasonSuite};

if (require.main === module) {
  runSuites([runOpenFile413ReasonSuite]);
}
