const {runSuites, sourceBetween} = require('./layout_test_helper');
const {runEditorPreviewSuite} = require('./editor_preview_suite');

runSuites([() => runEditorPreviewSuite({shardIndex: 1, shardCount: 3})]);
