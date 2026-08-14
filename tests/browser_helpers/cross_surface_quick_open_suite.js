const {
  assert,
  loadYolomux,
  canonical,
} = require('./layout_test_helper');

function registerCrossSurfaceQuickOpenSuite(test) {
  test('search-progress scope digest matches the server-side opaque root digest', () => {
    const api = loadYolomux('', ['1']);
    // Known SHA-256 test vector proves the synchronous implementation, then the 16-hex scope digest
    // must equal yolomux_lib/search/file_index.py::_root_scope_id = sha256(canonical_root)[:16].
    assert.equal(api.sha256HexForTest('abc'), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad', 'sha256 matches the published abc vector');
    assert.equal(api.fileSearchScopeIdForTest('/home/keivenc/dev'), 'c41cbfc443440bf6', 'the scope digest matches the backend _root_scope_id for /home/keivenc/dev');
    assert.equal(api.fileSearchScopeIdForTest(''), '', 'an empty realpath yields no scope digest');
  });

  test('a delta merge upserts, deletes, dedupes by realpath, and keeps roots isolated', () => {
    const api = loadYolomux('', ['1']);
    const start = [
      {path: '/repo/a.md', name: 'a.md', relative_path: 'a.md', realpath: '/repo/a.md'},
      {path: '/repo/b.md', name: 'b.md', relative_path: 'b.md', realpath: '/repo/b.md'},
    ];
    // An upsert REPLACES the row for its path; a delete REMOVES it; a brand-new upsert is appended.
    const merged = api.mergeFileQuickOpenChangesForTest(start, [
      {operation: 'delete', path: '/repo/b.md'},
      {operation: 'upsert', path: '/repo/a.md', name: 'a.md', relative_path: 'a.md', realpath: '/repo/a.md', size: 99},
      {operation: 'upsert', path: '/repo/c.md', name: 'c.md', relative_path: 'c.md', realpath: '/repo/c.md'},
    ], '/repo');
    assert.deepStrictEqual(canonical(merged.map(file => file.path)), ['/repo/a.md', '/repo/c.md'], 'delete drops b, upsert adds c, a survives');
    assert.equal(merged.find(file => file.path === '/repo/a.md').size, 99, 'an upsert replaces the prior row for its path');
    assert.equal(merged.every(file => file.indexed_root === '/repo'), true, 'every merged upsert is stamped with the producing root');

    // Two rows resolving to the SAME realpath (symlink/mirror) fold to one after rank-and-prune.
    const withMirror = api.mergeFileQuickOpenChangesForTest([], [
      {operation: 'upsert', path: '/repo/x.md', realpath: '/shared/x.md', name: 'x.md', relative_path: 'x.md'},
      {operation: 'upsert', path: '/link/x.md', realpath: '/shared/x.md', name: 'x.md', relative_path: 'x.md'},
    ], '/repo');
    const deduped = api.rankAndPruneFileQuickOpenCandidatesForTest(withMirror, 'x', 500);
    assert.equal(deduped.length, 1, 'two paths sharing one realpath fold to a single candidate');

    // A delete for one root cannot remove another root's identically-named-but-distinct-path row.
    const twoRoots = api.mergeFileQuickOpenChangesForTest(
      [{path: '/rootA/dup.md', name: 'dup.md', relative_path: 'dup.md', realpath: '/rootA/dup.md', indexed_root: '/rootA'}],
      [{operation: 'delete', path: '/rootB/dup.md'}], '/rootB');
    assert.deepStrictEqual(canonical(twoRoots.map(file => file.path)), ['/rootA/dup.md'], 'a delete only removes its own root\'s path');
  });

  test('a late exact-name delta can rank above earlier rows', () => {
    const api = loadYolomux('', ['1']);
    api.setFileQuickOpenCandidatesForTest('/repo', [
      {path: '/repo/deep/other/t5t-notes.md', name: 't5t-notes.md', relative_path: 'deep/other/t5t-notes.md', realpath: '/repo/deep/other/t5t-notes.md'},
    ]);
    api.setCommandPaletteStateForTest('files', 't5t.md');
    // A later delta publishes the exact-name file; after the merge it must rank ABOVE the earlier
    // fuzzy row (the render path re-ranks, so a late high-rank upsert is not stuck at the bottom).
    const merged = api.mergeFileQuickOpenChangesForTest(api.fileQuickOpenStateForTest().candidates, [
      {operation: 'upsert', path: '/repo/t5t.md', name: 't5t.md', relative_path: 't5t.md', realpath: '/repo/t5t.md'},
    ], '/repo');
    api.setFileQuickOpenCandidatesForTest('/repo', merged);
    const ranked = api.commandPaletteItems()
      .filter(item => item.category === 'file')
      .map(item => ({...item, score: api.commandPaletteItemScore(item, 't5t.md', {surface: 'files'})}))
      .filter(item => Number.isFinite(item.score))
      .sort((left, right) => right.score - left.score)
      .map(item => item.label);
    assert.equal(ranked[0], 't5t.md', 'the late exact-name match ranks first');
  });
}

module.exports = {registerCrossSurfaceQuickOpenSuite};
