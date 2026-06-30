const {spawn} = require('node:child_process');

// Each suite creates its own VM harness and is already runnable as a standalone test file. Keep the
// documented entry point, but let the independent processes use the available cores instead of making
// the gate wait for five serial harness loads.
const suiteFiles = [
  'tests/layout_restore.test.js',
  'tests/share_theme.test.js',
  'tests/editor_preview.test.js',
  'tests/tabber.test.js',
  'tests/layout_async.test.js',
];

function runSuite(file) {
  return new Promise(resolve => {
    const child = spawn(process.execPath, [file], {cwd: process.cwd()});
    let output = '';
    child.stdout.on('data', chunk => { output += chunk; });
    child.stderr.on('data', chunk => { output += chunk; });
    child.on('error', error => resolve({file, output: `${output}${error.stack || error}\n`, status: 1}));
    child.on('close', status => resolve({file, output, status: status || 0}));
  });
}

Promise.all(suiteFiles.map(runSuite)).then(results => {
  let failed = 0;
  for (const result of results) {
    process.stdout.write(`\n--- ${result.file} ---\n${result.output}`);
    if (result.status !== 0) failed += 1;
  }
  console.log(`\nlayout suite shards: ${results.length - failed} passed, ${failed} failed`);
  if (failed) process.exitCode = 1;
});
