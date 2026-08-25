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

function refusal(messageKey, fallback) {
  return jsonResponse({
    error: fallback,
    user_message: {key: messageKey, params: {}, fallback},
  }, 413);
}

async function flush(n = 8) { for (let i = 0; i < n; i++) await flushAsyncWork(); }

async function openWithRefusal(api, messageKey, fallback) {
  api.setFetchForTest((url) => {
    if (String(url).startsWith('/api/fs/read')) return Promise.resolve(refusal(messageKey, fallback));
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

  await testAsync('an oversize refusal carrying no reason still renders real numbers', async () => {
    // The observed payload named no message key, so the too-large state fell back to its own detail
    // template and supplied nothing for it. The user was shown the literal "{size}; limit is {limit}".
    const api = loadYolomux();
    api.setFetchForTest((url) => {
      if (String(url).startsWith('/api/fs/read')) {
        return Promise.resolve(jsonResponse({error: 'file too large'}, 413));
      }
      return Promise.resolve(jsonResponse({ok: true}));
    });
    await api.openFileInEditorForTest(MD, ENTRY, {viewMode: 'edit'});
    await flush();
    const state = api.currentFileStateForTest(MD) || {};
    assert.equal(state.kind, 'too-large', 'an unlabelled 413 on a read is still treated as oversize');
    // The detail the editor renders comes from this descriptor. Both names in the template have to
    // resolve, or the user reads the template instead of the numbers.
    const params = state.error?.user_message?.params || {};
    assert.equal(String(params.size || '').length > 0, true, 'the detail must carry the file size');
    assert.equal(String(params.limit || '').length > 0, true, 'the detail must carry the limit');
  });
}

module.exports = {runOpenFile413ReasonSuite};

if (require.main === module) {
  runSuites([runOpenFile413ReasonSuite]);
}
