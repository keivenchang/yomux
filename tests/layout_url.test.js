const {runSuites} = require('./layout_test_helper');
const {runLayoutRestoreSuite} = require('./layout_restore.test');
const {runShareThemeSuite} = require('./share_theme.test');
const {runEditorPreviewSuite} = require('./editor_preview.test');
const {runTabberSuite} = require('./tabber.test');
const {runLayoutAsyncSuite} = require('./layout_async.test');

runSuites([
  runLayoutRestoreSuite,
  runShareThemeSuite,
  runEditorPreviewSuite,
  runTabberSuite,
  runLayoutAsyncSuite,
]);
