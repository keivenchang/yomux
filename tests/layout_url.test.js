const {spawn} = require('node:child_process');

// The in-shard suite watchdog reports unsettled async tests at 60 seconds. Give it time to own that
// failure, then bound the separate class where a shard prints its summary but a referenced handle remains.
const SHARD_WATCHDOG_MS = 65_000;
const SHARD_TERMINATE_GRACE_MS = 2_000;

// Each suite creates its own VM harness and is already runnable as a standalone test file. Keep the
// documented entry point, but let the independent processes use the available cores instead of making
// the gate wait for five serial harness loads.
const allSuiteFiles = [
  'tests/i18n_structured_message.test.js',
  'tests/i18n_locale_registry.test.js',
  'tests/tmux_wall.test.js',
  'tests/layout_restore.test.js',
  'tests/drop_action_result.test.js',
  'tests/file_surface_menu.test.js',
  'tests/side_panes.test.js',
  'tests/cross_surface_state.test.js',
  'tests/editor_preview_core.test.js',
  'tests/editor_preview_tmux.test.js',
  'tests/editor_preview_settings.test.js',
  'tests/stats_current_ui.test.js',
  'tests/stats_current_panel.test.js',
  'tests/tabber.test.js',
  'tests/layout_async.test.js',
  'tests/backend_health_indicator.test.js',
  'tests/system_health_panel.test.js',
  'tests/diagnostic_redaction.test.js',
  'tests/gate_panels.test.js',
  'tests/open_file_missing_guard.test.js',
  'tests/open_file_413_reason.test.js',
];
// Keep this in step with NODE_LAYOUT_EXCLUDED_FILES in tools/test_catalog.py, which is what the gate
// actually passes as argv; tests/test_check_runner.py pins the two sets equal.
const defaultGateExcludedSuiteFiles = new Set([
  // gate_panels pins the decorator prose of tests/test_gate_panels.py, whose own xfail(strict=True)
  // markers already own that guarantee. It is written to go red when F9 SubsystemSpec lands.
  'tests/gate_panels.test.js',
]);
const suiteFiles = process.argv.length > 2
  ? process.argv.slice(2)
  : allSuiteFiles.filter(file => !defaultGateExcludedSuiteFiles.has(file));

function shardSummaryState(output) {
  const summaries = String(output || '')
    .split(/\r?\n/)
    .map(line => line.match(/\bsuite:\s*(?:(\d+) passed, (\d+) failed|passed)\s*$/))
    .filter(Boolean)
    .map(match => ({
      failed: match[2] === undefined ? 0 : Number(match[2]),
    }));
  return {
    count: summaries.length,
    failed: summaries.reduce((total, summary) => total + summary.failed, 0),
    successful: summaries.length === 1 && summaries[0].failed === 0,
  };
}

function runSuite(file, spawnChild = spawn, options = {}) {
  const timeoutMs = Number.isFinite(options.timeoutMs) ? Math.max(1, Number(options.timeoutMs)) : SHARD_WATCHDOG_MS;
  const terminateGraceMs = Number.isFinite(options.terminateGraceMs)
    ? Math.max(1, Number(options.terminateGraceMs))
    : SHARD_TERMINATE_GRACE_MS;
  return new Promise(resolve => {
    const child = spawnChild(process.execPath, [file], {cwd: process.cwd()});
    let output = '';
    let settled = false;
    let timedOut = false;
    let watchdog = null;
    let terminateTimer = null;
    const finish = (code, signal, error = null) => {
      if (settled) return;
      settled = true;
      if (watchdog) clearTimeout(watchdog);
      if (terminateTimer) clearTimeout(terminateTimer);
      if (error) output += `${error.stack || error}\n`;
      const summary = shardSummaryState(output);
      const status = !timedOut && code === 0 && !signal && summary.successful ? 0 : 1;
      let summaryFailure = '';
      if (summary.count === 0) summaryFailure = '\n✗ shard exited without a suite summary\n';
      else if (summary.count > 1) summaryFailure = `\n✗ shard printed ${summary.count} suite summaries\n`;
      else if (summary.failed > 0) summaryFailure = `\n✗ shard suite summary reported ${summary.failed} failed\n`;
      resolve({file, output: `${output}${summaryFailure}`, status});
    };
    child.stdout.on('data', chunk => { output += chunk; });
    child.stderr.on('data', chunk => { output += chunk; });
    child.on('error', error => finish(null, null, error));
    child.on('close', (code, signal) => finish(code, signal));
    watchdog = setTimeout(() => {
      if (settled) return;
      timedOut = true;
      const summaryState = shardSummaryState(output).count > 0
        ? 'after printing a suite summary'
        : 'without printing a suite summary';
      output += `\n✗ shard exceeded ${timeoutMs} ms watchdog ${summaryState}\n`;
      child.kill('SIGTERM');
      terminateTimer = setTimeout(() => {
        if (!settled) child.kill('SIGKILL');
      }, terminateGraceMs);
    }, timeoutMs);
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

module.exports = {allSuiteFiles, defaultGateExcludedSuiteFiles, runAllSuites, runSuite, suiteFiles};

if (require.main === module) runAllSuites();
