const assert = require('node:assert/strict');
const fs = require('node:fs');

let passed = 0;
let failed = 0;
function test(label, fn) {
  try {
    fn();
    passed += 1;
  } catch (error) {
    failed += 1;
    process.exitCode = 1;
    console.error(`✗ ${label}: ${error.message}`);
  }
}

const stateSource = fs.readFileSync('static_src/js/yolomux/93_share_state.js', 'utf8');
const replaySource = fs.readFileSync('static_src/js/yolomux/94_share_replay.js', 'utf8');
const finderSeed = stateSource.slice(stateSource.indexOf('function shareFinderSeed()'), stateSource.indexOf('function shareSessionsFromLayout()'));
const finderApply = replaySource.slice(replaySource.indexOf('async function applyShareFinderState'), replaySource.indexOf('function applySharePreferencesState'));
const legacyApply = replaySource.slice(replaySource.indexOf('function applyLegacyShareFinderMode'), replaySource.indexOf('function applySharePreferencesState'));

test('share seeds use layout identities rather than the retired global mode', () => {
  assert.doesNotMatch(finderSeed, /mode:\s*normalizeFileExplorerMode\(fileExplorerMode\)/);
  assert.match(replaySource, /shareLayoutSeed\(\)[\s\S]*layout: seed\.layout,[\s\S]*tabs: seed\.tabs/);
});

test('legacy finder mode is passive and cannot rebuild or select every surface', () => {
  assert.match(finderApply, /`finder\.mode` is a legacy semantic frame field/);
  assert.doesNotMatch(finderApply, /fileExplorerMode\s*=/);
  assert.doesNotMatch(finderApply, /applyFileExplorerMode\(/);
  assert.match(legacyApply, /fileExplorerChangesSelectedSession = session/);
  assert.doesNotMatch(legacyApply, /switchFileExplorerChangesSession|activatePaneTab|applyLayoutSlots|renderFileExplorerChangesPanels/);
});

test('share refreshes only independently present file surfaces', () => {
  assert.match(finderApply, /itemInLayout\(finderItemId\)/);
  assert.match(finderApply, /itemInLayout\(differItemId\)[\s\S]*renderFileExplorerChangesPanels/);
  assert.match(finderApply, /itemInLayout\(tabberItemId\)[\s\S]*refreshTabberPanels/);
  assert.match(finderApply, /restoreShareScrollTargetByKey\('finder:finder'\)/);
  assert.match(finderApply, /restoreShareScrollTargetByKey\('finder:differ'\)/);
  assert.match(finderApply, /restoreShareScrollTargetByKey\('finder:tabber'\)/);
});

console.log(`\nshare file-surface replay suite: ${passed} passed, ${failed} failed`);
