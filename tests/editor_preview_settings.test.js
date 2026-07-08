const {runSuites} = require('./layout_test_helper');
const {runEditorPreviewSuite} = require('./editor_preview_suite');

runSuites([() => runEditorPreviewSuite({shardIndex: 2, shardCount: 3})]);
