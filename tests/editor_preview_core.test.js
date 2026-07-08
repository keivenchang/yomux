const {runSuites} = require('./layout_test_helper');
const {runEditorPreviewSuite} = require('./editor_preview_suite');

runSuites([() => runEditorPreviewSuite({shardIndex: 0, shardCount: 3})]);
