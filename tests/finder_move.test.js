// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path').posix;
const vm = require('node:vm');
const source = fs.readFileSync('static_src/js/yomux/45_file_explorer_actions.js', 'utf8');
const coreSource = fs.readFileSync('static_src/js/yomux/10_core_utils.js', 'utf8');
const escapeSource = coreSource.slice(coreSource.indexOf('function esc('), coreSource.indexOf('const disclosureChevronGlyph'));
const diffSource = source.slice(source.indexOf('function diffDialogLines('), source.indexOf('function fileCompareLineHtml('));
const treeSource = fs.readFileSync('static_src/js/yomux/40_file_explorer_files.js', 'utf8');
const mapping = treeSource.slice(treeSource.indexOf('// Successful namespace changes share'));
const block = source.slice(source.indexOf('// Finder owns file moves'), source.indexOf('async function refreshFileExplorerTrees('));
function setup(answer = 'no') {
  const listeners = {};
  const calls = [];
  const mappings = [];
  const context = {
    Set, Map, URL, Error, console,
    t: (key, params = {}) => `${key}:${JSON.stringify(params)}`,
    normalizeDirectoryPath: value => path.normalize(value).replace(/\/$/, '') || '/',
    dirnameOf: path.dirname, basenameOf: path.basename,
    readOnlyMode: false,
    document: {addEventListener: (type, fn) => (listeners[type] ||= []).push(fn), querySelectorAll: () => []},
    fileExplorerSelectedPaths: new Set(['/from/a', '/from/a/child']),
    fileExplorerExpanded: new Set(['/from/a']),
    fileExplorerSyncUserExpansionState: new Map(), fileExplorerSyncManualCollapsedPaths: new Set(), fileExplorerSyncTargetRecords: new Map(), fileExplorerSelectionLead: null,
    fileExplorerSelectionAnchor: '/from/a/child', fileTreeRenamePath: null,
    fileEditorSaveOwners: new Map(), fileState: new Map(),
    showFileEditorDecisionDialog: async options => { calls.push(['modal', options]); return answer; },
    apiFetchJson: async (url, options) => { calls.push(['request', url, JSON.parse(options.body)]); return {complete: true, moved: [{old_path: '/from/a', path: '/to/a'}]}; },
    fileErrorMessageSnapshot: error => String(error),
    syncOpenFileContentFromPanels: () => {},
    renameOpenFilePath: (oldPath, newPath) => mappings.push([oldPath, newPath]),
    markFileIndexRootsRefreshing: () => {}, invalidateFileExplorerRoots: () => {},
    refreshFileExplorerTrees: async () => {}, rescheduleAllFileAutosaves: () => {},
    showLayoutStatus: message => calls.push(['status', message]),
    statusErr: message => calls.push(['error', message]),
    localizedHtml: (key, params) => `${key}: ${params.error}`,
    clearDropPreview: () => {}, hasYomuxDragData: () => false,
    fileDragPayload: () => null, currentFileExplorerRoot: () => '/root',
  };
  vm.createContext(context);
  vm.runInContext(`${escapeSource}\n${diffSource}\n${mapping}\n${block}\nthis.moveSources = finderMoveSources; this.move = confirmFinderMove; this.pending = fileTreeMovePendingForPath; this.target = finderMoveDropTarget;`, context);
  return {context, calls, mappings, listeners};
}
const transfer = text => ({types: ['text/plain'], files: [], getData: type => type === 'text/plain' ? text : ''});
(async () => {
  for (const directory of [false, true]) {
    const root = directory ? '/delete/folder' : '/delete/file.txt';
    const target = directory ? `${root}/child.txt` : root;
    for (const succeeds of [false, true]) {
      let settle;
      const dirty = {dirty: true, content: 'unsaved local edit'};
      const clean = {dirty: false, content: 'disk bytes'};
      const sibling = '/delete/independent.txt';
      const states = new Map([[target, dirty], [sibling, clean]]);
      const c = {
        readOnlyMode: false, compactNestedPaths: items => items, t: key => key, tPlural: key => key,
        window: {confirm: () => true}, confirmLargeDirectoryDeletes: async () => true,
        apiFetchJson: () => new Promise((resolve, reject) => { settle = () => succeeds ? resolve({deleted: true}) : reject(new Error('denied')); }),
        fileState: states, fileExplorerSelectedPaths: new Set(), fileExplorerSelectionAnchor: null,
        basenameOf: path.basename, dirnameOf: path.dirname,
        removeOpenFile: name => states.delete(name), syncOpenFileContentFromPanels() {},
        markOpenFileMissing: name => { states.get(name).externalMissing = true; },
        markFileExplorerDeletedPaths() {}, invalidateFileExplorerRoots() {}, removeFileTreePathsImmediately() {},
        clearFileExplorerDeletedPaths() {}, refreshFileExplorerTrees: async () => {},
        renderSessionButtons() {}, renderPaneTabStrips() {}, showLayoutStatus() {}, statusErr() {}, localizedHtml: key => key,
      };
      vm.createContext(c);
      const start = source.indexOf('async function deleteFileTreePath(');
      vm.runInContext(source.slice(start, source.indexOf('\nfunction ', start)), c);
      const pending = c.deleteFileTreePath(root, {kind: directory ? 'dir' : 'file'}, [root]);
      await new Promise(setImmediate);
      assert.equal(states.get(target), dirty, 'a pending delete must retain unsaved text');
      settle();
      await pending;
      assert.equal(states.get(target), dirty, 'delete completion must retain unsaved text');
      assert.equal(dirty.content, 'unsaved local edit');
      assert.equal(Boolean(dirty.externalMissing), succeeds, 'only successful deletion marks the buffer missing');
      assert.equal(states.get(sibling), clean, 'unrelated open files are unchanged');
    }
  }
  let {context, calls} = setup();
  assert.equal(context.finderMoveDestinationHtml('/a/b/c/d', '/a/b/x/y'), '/a/b/<strong>x/y</strong>');
  assert.equal(context.finderMoveDestinationHtml('/a/b/c/file.md', '/a/b/x/file.md'), '/a/b/<strong>x</strong>/file.md');
  assert.equal(context.finderMoveDestinationHtml('/a/b/file.md', '/a/file.md'), '/a/file.md');
  assert.equal(context.finderMoveDestinationHtml('/a/file.md', '/a/x/file.md'), '/a/<strong>x</strong>/file.md');
  assert.equal(context.finderMoveDestinationHtml('/a/file.md', '/a/<img>/file.md'), '/a/<strong>&lt;img&gt;</strong>/file.md');
  assert.equal(context.finderMoveDestinationHtml('/a/b/c/d/file.md', '/a/x/c/y/file.md'), '/a/<strong>x</strong>/c/<strong>y</strong>/file.md');
  assert.equal(context.finderMoveTargetPath('/a/file.md', '/'), '/file.md');
  assert.equal(context.finderMoveDestinationHtml('/a/file.md', '/file.md'), '/file.md');
  assert.equal(context.finderMoveDestinationHtml('/a/a/file.md', '/a/b/a/file.md'), '/a/<strong>b</strong>/a/file.md');
  const batch = context.finderMoveConfirmationHtml(['/from/one.md', '/from/two.md'], '/to');
  assert.equal((batch.match(/class="finder-move-comparison"/g) || []).length, 2);
  assert(batch.includes('/<strong>to</strong>/one.md') && batch.includes('/<strong>to</strong>/two.md'));
  assert.deepEqual([...context.moveSources(transfer('/a\n/a/b\n/a\n/b c'))], ['/a', '/b c']);
  assert.deepEqual([...context.moveSources(transfer('file:///tmp/a%20b'))], ['/tmp/a b']);
  for (const text of ['relative', 'https://example.com/a', 'file://other-host/a', 'file:///a#b', '']) assert.throws(() => context.moveSources(transfer(text)));
  assert.throws(() => context.moveSources({types: ['Files'], files: [{name: 'a'}]}));
  await context.move(['/from/a'], '/to');
  assert.equal(calls.filter(call => call[0] === 'request').length, 0, 'No must never mutate');
  assert.equal(calls[0][1].focusSelector, '[data-dialog-action="no"]');
  ({context, calls} = setup('yes'));
  let release;
  context.apiFetchJson = async () => { calls.push(['request']); return new Promise(resolve => { release = resolve; }); };
  const first = context.move(['/from/a'], '/to');
  await new Promise(setImmediate);
  assert(context.pending('/from/a/child'));
  assert(context.pending('/to/a/child'));
  assert.equal(await context.move(['/from/a'], '/to'), false, 'pending operation cannot submit twice');
  release({complete: true, moved: [{old_path: '/from/a', path: '/to/a'}]});
  assert.equal(await first, true);
  assert(!context.pending('/from/a'));
  assert.deepEqual([...context.fileExplorerSelectedPaths], ['/to/a', '/to/a/child']);
  assert.deepEqual([...context.fileExplorerExpanded], ['/to/a']);
  for (const owner of [{active: {}}, {pending: {}}, {conflict: true}]) {
    ({context, calls} = setup('yes'));
    context.fileEditorSaveOwners.set('/from/a/child', owner);
    assert.equal(await context.move(['/from/a'], '/to'), false);
    assert(!calls.some(call => call[0] === 'request'), 'unresolved save must block move');
  }
  ({context, calls} = setup('yes'));
  context.apiFetchJson = async () => ({complete: false, moved: [{old_path: '/from/a', path: '/to/a'}], failed: {error: 'collision'}, unattempted: ['/from/b']});
  assert.equal(await context.move(['/from/a', '/from/b'], '/to'), false);
  assert(context.fileExplorerSelectedPaths.has('/to/a'), 'successful subset is applied before reporting failure');
  assert(calls.some(call => call[0] === 'error' && call[1].includes('collision')));
  for (const destination of ['/from', '/from/a', '/from/a/child']) {
    ({context, calls} = setup('yes'));
    await context.move(['/from/a'], destination);
    assert(!calls.some(call => call[0] === 'request'));
  }
  for (const target of ['/to/a', '/to/a/child']) {
    ({context, calls} = setup('yes'));
    const dirty = {content: 'unsaved destination', dirty: true};
    context.fileState.set(target, dirty);
    assert.equal(await context.move(['/from/a'], '/to'), false);
    assert(!calls.some(call => call[0] === 'request'));
    assert.equal(context.fileState.get(target), dirty);
  }
  ({context, calls} = setup('yes'));
  const dirty = {content: 'unsaved source', dirty: true};
  context.fileState.set('/from/a', dirty);
  context.apiFetchJson = async () => { throw new Error('unknown outcome'); };
  assert.equal(await context.move(['/from/a'], '/to'), false);
  assert(dirty.externalError.includes('unknown outcome'), 'unknown outcome suppresses stale-path autosave');
  ({context, calls} = setup('yes'));
  const trailing = {content: 'trailing source edits', dirty: true};
  context.fileState.set('/from/b', trailing);
  context.apiFetchJson = async () => ({complete: true, moved: [{old_path: '/from/a', path: '/to/a'}, {old_path: '/from/b', path: '/to/b'}]});
  context.applyFileTreePathMapping = () => { throw new Error('late collision'); };
  assert.equal(await context.move(['/from/a', '/from/b'], '/to'), false);
  assert(trailing.externalError.includes('late collision'), 'unmapped trailing buffer cannot autosave to old path');
  const {loadYomux} = require('./browser_helpers/layout_test_helper');
  const api = loadYomux('?layout=left&tabs=left:1', ['1']);
  api.setOpenFileStateForTest('/from/a', {kind: 'text', content: 'source edits', original: 'source', dirty: true});
  api.setOpenFileStateForTest('/to/a', {kind: 'text', content: 'destination edits', original: '', dirty: true});
  assert.equal(api.renameOpenFilePathForTest('/from/a', '/to/a'), false, 'blocked relocation retains both buffers');
  assert.equal(api.fileEditorStateForItemForTest('/to/a').content, 'destination edits');
  assert.equal(api.fileEditorStateForItemForTest('/from/a').content, 'source edits');
  console.log('finder move suite: passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
