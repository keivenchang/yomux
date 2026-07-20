const {spawn} = require('node:child_process');

// Each suite creates its own VM harness and is already runnable as a standalone test file. Keep the
// documented entry point, but let the independent processes use the available cores instead of making
// the gate wait for five serial harness loads.
const suiteFiles = [
  'tests/i18n_structured_message.test.js',
  'tests/i18n_locale_registry.test.js',
  'tests/tmux_wall.test.js',
  'tests/layout_restore.test.js',
  'tests/drop_action_result.test.js',
  'tests/file_surface_menu.test.js',
  'tests/side_panes.test.js',
  'tests/share_theme.test.js',
  'tests/share_file_surface_replay.test.js',
  'tests/editor_preview_core.test.js',
  'tests/editor_preview_tmux.test.js',
  'tests/editor_preview_settings.test.js',
  'tests/stats_current_ui.test.js',
  'tests/stats_current_panel.test.js',
  'tests/tabber.test.js',
  'tests/layout_async.test.js',
];

function runSuite(file, spawnChild = spawn) {
  return new Promise(resolve => {
    const child = spawnChild(process.execPath, [file], {cwd: process.cwd()});
    let output = '';
    child.stdout.on('data', chunk => { output += chunk; });
    child.stderr.on('data', chunk => { output += chunk; });
    child.on('error', error => resolve({file, output: `${output}${error.stack || error}\n`, status: 1}));
    child.on('close', (code, signal) => {
      const hasSummary = /\bsuite:\s*(?:\d+ passed, \d+ failed|passed)\b/.test(output);
      const status = code === 0 && !signal && hasSummary ? 0 : 1;
      const missingSummary = !hasSummary ? '\n✗ shard exited without a suite summary\n' : '';
      resolve({file, output: `${output}${missingSummary}`, status});
    });
  });
}

function runAllSuites() {
  return Promise.all(suiteFiles.map(file => runSuite(file))).then(results => {
  let failed = 0;
  for (const result of results) {
    process.stdout.write(`\n--- ${result.file} ---\n${result.output}`);
    if (result.status !== 0) failed += 1;
  }
  console.log(`\nlayout suite shards: ${results.length - failed} passed, ${failed} failed`);
  if (failed) process.exitCode = 1;
  });
}

module.exports = {runAllSuites, runSuite, suiteFiles};

if (require.main === module) runAllSuites();
